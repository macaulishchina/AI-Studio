"""
MCP Client Manager — 连接生命周期管理

管理与 MCP Server 的连接:
  - stdio: 通过 subprocess 启动子进程, 通过 stdin/stdout 通信
  - sse: 通过 HTTP SSE 连接远程 MCP Server
  - streamable_http: 通过 HTTP POST/GET 通信

核心能力:
  - 按需启动/连接 MCP Server
  - 健康检查 (ping/pong)
  - 自动重连/重启
  - 工具发现 (tools/list)
  - 工具调用 (tools/call)
  - 优雅关闭

JSON-RPC 2.0 协议实现 (MCP 基于 JSON-RPC 2.0):
  请求: {"jsonrpc":"2.0", "id":N, "method":"...", "params":{...}}
  响应: {"jsonrpc":"2.0", "id":N, "result":{...}} 或 {"jsonrpc":"2.0", "id":N, "error":{...}}

注意: Windows 上 uvicorn 使用 SelectorEventLoop, 不支持 asyncio.create_subprocess_exec.
因此本模块使用 subprocess.Popen (同步进程) + run_in_executor (线程包装) 的方式,
在任何事件循环下均可正常工作。
"""
import asyncio
import json
import logging
import os
import shutil
import subprocess
import threading
from typing import Any, Dict, List, Optional

from studio.backend.services.mcp.registry import MCPServerConfig

logger = logging.getLogger(__name__)

# JSON-RPC 请求 ID 计数器
_request_id_counter = 0


def _next_request_id() -> int:
    global _request_id_counter
    _request_id_counter += 1
    return _request_id_counter


class MCPClientConnection:
    """与单个 MCP Server 的连接

    使用 subprocess.Popen (同步) 替代 asyncio.create_subprocess_exec,
    通过 run_in_executor 将阻塞 I/O 转为异步, 兼容 Windows SelectorEventLoop。
    """

    def __init__(self, config: MCPServerConfig, env_override: Optional[Dict[str, str]] = None):
        self.config = config
        self.env_override = env_override or {}
        self._process: Optional[subprocess.Popen] = None
        self._connected = False
        self._lock = asyncio.Lock()
        self._pending: Dict[int, asyncio.Future] = {}  # request_id → Future
        self._reader_task: Optional[asyncio.Task] = None
        self._server_info: Dict[str, Any] = {}
        self._server_capabilities: Dict[str, Any] = {}
        self._write_lock = threading.Lock()  # 保护 stdin 写入的线程锁
        self.last_error: str = ""

    @property
    def is_connected(self) -> bool:
        if self.config.transport == "stdio":
            return (
                self._process is not None
                and self._process.poll() is None
                and self._connected
            )
        return self._connected

    async def connect(self) -> bool:
        """建立连接 (启动子进程或 HTTP 连接)"""
        async with self._lock:
            if self.is_connected:
                return True

            if self.config.transport == "stdio":
                return await self._connect_stdio()
            else:
                logger.warning(f"MCP Client: {self.config.slug} 暂不支持 {self.config.transport} 传输")
                return False

    async def _connect_stdio(self) -> bool:
        """通过 stdio 启动子进程并建立 JSON-RPC 连接

        使用 subprocess.Popen (同步) 代替 asyncio.create_subprocess_exec,
        以兼容 Windows SelectorEventLoop (uvicorn --reload 默认)。
        """
        try:
            # 构建环境变量
            env = {**os.environ}
            # 先注入模板环境变量
            for k, v in self.config.env.items():
                env[k] = v
            # 再注入运行时覆盖 (如动态 token)
            for k, v in self.env_override.items():
                env[k] = v

            command = self.config.command
            args = self.config.args or []

            # 解析命令路径 (Windows 兼容: npx → npx.cmd)
            resolved_command = command
            if os.name == "nt":
                if command.lower() == "npx":
                    resolved_command = shutil.which("npx.cmd") or shutil.which("npx") or command
                elif command.lower() == "node":
                    resolved_command = shutil.which("node.cmd") or shutil.which("node") or command
                else:
                    resolved_command = shutil.which(command) or command
            else:
                resolved_command = shutil.which(command) or command

            logger.info(f"MCP Client: 启动 {self.config.slug} ({resolved_command} {' '.join(args)})")

            # 使用 subprocess.Popen — 兼容任何事件循环
            self._process = subprocess.Popen(
                [resolved_command] + args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                bufsize=0,  # unbuffered
            )

            # 快速失败检测: 子进程秒退
            await asyncio.sleep(0.2)
            if self._process.poll() is not None:
                stderr_text = self._read_stderr_excerpt_sync()
                self.last_error = (
                    f"进程启动后立即退出 (code={self._process.returncode})"
                    + (f"; stderr={stderr_text}" if stderr_text else "")
                )
                logger.error(
                    f"MCP Client: {self.config.slug} 启动后立即退出 "
                    f"(code={self._process.returncode}) stderr={stderr_text}"
                )
                await self.disconnect()
                return False

            # 启动 stdout 读取协程
            self._reader_task = asyncio.create_task(
                self._read_stdout_loop(),
                name=f"mcp-reader-{self.config.slug}",
            )

            # 发送 initialize 请求
            init_result = await self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "studio",
                    "version": "1.0.0",
                },
            }, timeout=30)

            if init_result is None:
                stderr_text = self._read_stderr_excerpt_sync()
                self.last_error = "initialize 无响应" + (f"; stderr={stderr_text}" if stderr_text else "")
                logger.error(f"MCP Client: {self.config.slug} initialize 失败: {stderr_text}")
                await self.disconnect()
                return False

            self._server_info = init_result.get("serverInfo", {})
            self._server_capabilities = init_result.get("capabilities", {})

            # 发送 initialized 通知
            await self._send_notification("notifications/initialized", {})

            self._connected = True
            logger.info(
                f"✅ MCP Client: {self.config.slug} 已连接 "
                f"(server: {self._server_info.get('name', '?')} "
                f"v{self._server_info.get('version', '?')})"
            )
            return True

        except FileNotFoundError:
            self.last_error = f"命令未找到: {self.config.command} (PATH 中未找到可执行文件)"
            logger.error(
                f"MCP Client: {self.config.slug} 启动失败 — "
                f"命令未找到: {self.config.command}"
            )
            return False
        except Exception as e:
            stderr_text = ""
            try:
                stderr_text = self._read_stderr_excerpt_sync()
            except Exception:
                pass
            self.last_error = f"{type(e).__name__}: {repr(e)}"
            if stderr_text:
                self.last_error += f"; stderr={stderr_text}"
            extra = f" | stderr={stderr_text}" if stderr_text else ""
            logger.exception(f"MCP Client: {self.config.slug} 连接失败: {e}{extra}")
            await self.disconnect()
            return False

    def _read_stderr_excerpt_sync(self, limit: int = 800) -> str:
        """同步读取子进程 stderr 摘要（用于连接失败诊断）"""
        if not self._process or not self._process.stderr:
            return ""
        try:
            if self._process.poll() is None:
                # 进程仍在运行时避免阻塞读取
                return ""
            raw = self._process.stderr.read()
            if not raw:
                return ""
            text = raw.decode("utf-8", errors="replace").strip()
            if len(text) > limit:
                return text[:limit] + "..."
            return text
        except Exception:
            return ""

    def _blocking_readline(self) -> Optional[bytes]:
        """在线程中同步读取 stdout 一行 — 返回 None 表示 EOF"""
        if not self._process or not self._process.stdout:
            return None
        try:
            line = self._process.stdout.readline()
            if not line:
                return None  # EOF
            return line
        except (OSError, ValueError):
            return None

    def _blocking_read(self, n: int) -> Optional[bytes]:
        """在线程中同步读取 stdout 指定字节数"""
        if not self._process or not self._process.stdout:
            return None
        try:
            data = self._process.stdout.read(n)
            if not data:
                return None
            return data
        except (OSError, ValueError):
            return None

    def _blocking_write(self, data: bytes) -> bool:
        """在线程中同步写入 stdin (线程安全)"""
        if not self._process or not self._process.stdin:
            return False
        try:
            with self._write_lock:
                self._process.stdin.write(data)
                self._process.stdin.flush()
            return True
        except (OSError, ValueError, BrokenPipeError):
            return False

    async def _async_readline(self) -> Optional[bytes]:
        """异步读取一行 (通过线程池)"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._blocking_readline)

    async def _async_read(self, n: int) -> Optional[bytes]:
        """异步读取 n 字节 (通过线程池)"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._blocking_read, n)

    async def _async_write(self, data: bytes) -> bool:
        """异步写入 (通过线程池)"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._blocking_write, data)

    async def _read_stdout_loop(self):
        """持续读取子进程 stdout, 解析 JSON-RPC 响应

        兼容行分隔 JSON 和 Content-Length 帧。
        """
        try:
            while True:
                msg = await self._read_mcp_message()
                if msg is None:
                    break  # EOF — 进程已退出

                # 处理响应
                if "id" in msg and msg["id"] in self._pending:
                    req_id = msg["id"]
                    fut = self._pending.pop(req_id)
                    if not fut.done():
                        if "error" in msg:
                            fut.set_result({"_error": msg["error"]})
                        else:
                            fut.set_result(msg.get("result"))
                elif "method" in msg:
                    # 服务端发来的通知/请求 (忽略或记录)
                    logger.debug(f"MCP Client {self.config.slug} 收到通知: {msg.get('method')}")

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"MCP Client {self.config.slug} 读取异常: {e}")
        finally:
            self._connected = False

    async def _read_mcp_message(self) -> Optional[Dict[str, Any]]:
        """读取一条 MCP 消息（行分隔 JSON 或 Content-Length 帧）"""

        # 读取首行（可能是 header，也可能是 JSON 行）
        first = await self._async_readline()
        if first is None:
            return None

        first_text = first.decode("utf-8", errors="replace").strip("\r\n")
        if not first_text:
            return {}

        # 兼容模式：行分隔 JSON
        if first_text.startswith("{"):
            try:
                return json.loads(first_text)
            except json.JSONDecodeError:
                logger.debug(f"MCP Client {self.config.slug} 非 JSON 输出: {first_text[:200]}")
                return {}

        # 标准模式：Content-Length header + body
        headers: Dict[str, str] = {}
        line = first_text
        while True:
            if not line:
                break
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
            nxt = await self._async_readline()
            if nxt is None:
                return None
            line = nxt.decode("utf-8", errors="replace").strip("\r\n")

        content_len = int(headers.get("content-length", "0") or "0")
        if content_len <= 0:
            return {}

        body = await self._async_read(content_len)
        if body is None or len(body) < content_len:
            return None

        try:
            return json.loads(body.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            logger.debug(f"MCP Client {self.config.slug} JSON 解码失败")
            return {}

    async def _send_request(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: float = 60,
    ) -> Optional[Dict[str, Any]]:
        """发送 JSON-RPC 请求并等待响应"""
        if not self._process or not self._process.stdin:
            return None

        req_id = _next_request_id()
        request = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
        }
        if params is not None:
            request["params"] = params

        # 创建 Future 用于接收响应
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[req_id] = fut

        try:
            # 行分隔 JSON 写入（与 @modelcontextprotocol/server-github 兼容）
            data = (json.dumps(request) + "\n").encode("utf-8")
            ok = await self._async_write(data)
            if not ok:
                self._pending.pop(req_id, None)
                return None

            result = await asyncio.wait_for(fut, timeout=timeout)

            if isinstance(result, dict) and "_error" in result:
                error = result["_error"]
                logger.warning(f"MCP RPC 错误 ({self.config.slug}.{method}): {error}")
                return None

            return result

        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            logger.warning(f"MCP RPC 超时 ({self.config.slug}.{method}, {timeout}s)")
            return None
        except Exception as e:
            self._pending.pop(req_id, None)
            logger.error(f"MCP RPC 发送失败 ({self.config.slug}.{method}): {e}")
            return None

    async def _send_notification(self, method: str, params: Optional[Dict[str, Any]] = None):
        """发送 JSON-RPC 通知 (无需响应)"""
        if not self._process or not self._process.stdin:
            return

        notification = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            notification["params"] = params

        try:
            data = (json.dumps(notification) + "\n").encode("utf-8")
            await self._async_write(data)
        except Exception as e:
            logger.debug(f"MCP 通知发送失败 ({self.config.slug}.{method}): {e}")

    async def list_tools(self) -> List[Dict[str, Any]]:
        """调用 MCP tools/list 获取工具列表"""
        result = await self._send_request("tools/list", {})
        if result and "tools" in result:
            return result["tools"]
        return []

    async def call_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        timeout: float = 120,
    ) -> Dict[str, Any]:
        """调用 MCP 工具

        Returns:
            {"content": [...], "isError": bool}  MCP 标准响应格式
            或 {"error": "..."} 内部错误
        """
        result = await self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        }, timeout=timeout)

        if result is None:
            return {"error": f"MCP 工具调用失败: {tool_name} (无响应)"}

        return result

    async def ping(self) -> bool:
        """健康检查"""
        try:
            result = await self._send_request("ping", timeout=5)
            return result is not None or result == {}
        except Exception:
            return False

    async def disconnect(self):
        """断开连接 / 停止子进程"""
        self._connected = False

        # 取消读取任务
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass

        # 终止子进程 (subprocess.Popen)
        if self._process and self._process.poll() is None:
            try:
                self._process.terminate()
                # 在线程中等待进程退出, 避免阻塞事件循环
                loop = asyncio.get_running_loop()
                try:
                    await asyncio.wait_for(
                        loop.run_in_executor(None, self._process.wait),
                        timeout=5,
                    )
                except asyncio.TimeoutError:
                    self._process.kill()
                    await loop.run_in_executor(None, self._process.wait)
            except Exception:
                pass

        # 清理待处理请求
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()

        logger.info(f"MCP Client: {self.config.slug} 已断开")

    @property
    def server_info(self) -> Dict[str, Any]:
        return self._server_info

    @property
    def server_capabilities(self) -> Dict[str, Any]:
        return self._server_capabilities


class MCPClientManager:
    """MCP 连接池管理器 — 维护所有 MCP Server 连接"""

    _instance: Optional["MCPClientManager"] = None

    def __init__(self):
        self._connections: Dict[str, MCPClientConnection] = {}
        self._last_errors: Dict[str, str] = {}
        self._lock = asyncio.Lock()

    @classmethod
    def get_instance(cls) -> "MCPClientManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def get_or_connect(
        self,
        config: MCPServerConfig,
        env_override: Optional[Dict[str, str]] = None,
    ) -> Optional[MCPClientConnection]:
        """获取已有连接或创建新连接

        env_override 用于动态注入凭据 (如不同 workspace 的 GitHub token)
        """
        async with self._lock:
            slug = config.slug
            conn = self._connections.get(slug)

            # 已有活跃连接
            if conn and conn.is_connected:
                return conn

            # 需要 (重新) 连接
            if conn:
                await conn.disconnect()

            conn = MCPClientConnection(config, env_override)
            ok = await conn.connect()
            if ok:
                self._connections[slug] = conn
                self._last_errors.pop(slug, None)

                # 连接成功后自动发现工具
                try:
                    tools = await conn.list_tools()
                    from studio.backend.services.mcp.registry import MCPServerRegistry
                    MCPServerRegistry.get_instance().update_discovered_tools(slug, tools)
                    logger.info(f"MCP Client {slug}: 发现 {len(tools)} 个工具")
                except Exception as e:
                    logger.warning(f"MCP Client {slug}: 工具发现失败: {e}")

                return conn
            else:
                if conn.last_error:
                    self._last_errors[slug] = conn.last_error
                return None

    def get_connection(self, slug: str) -> Optional[MCPClientConnection]:
        """获取已有连接 (不自动创建)"""
        conn = self._connections.get(slug)
        if conn and conn.is_connected:
            return conn
        return None

    async def disconnect_all(self):
        """断开所有连接 (应用关闭时调用)"""
        async with self._lock:
            for slug, conn in self._connections.items():
                try:
                    await conn.disconnect()
                except Exception as e:
                    logger.warning(f"MCP Client {slug} 断开失败: {e}")
            self._connections.clear()
            logger.info("MCP ClientManager: 所有连接已关闭")

    async def disconnect(self, slug: str):
        """断开指定 server 连接"""
        async with self._lock:
            conn = self._connections.pop(slug, None)
            if conn:
                await conn.disconnect()

    def get_last_error(self, slug: str) -> str:
        return self._last_errors.get(slug, "")

    async def health_check(self) -> Dict[str, Dict[str, Any]]:
        """所有连接健康检查"""
        result = {}
        for slug, conn in self._connections.items():
            if conn.is_connected:
                try:
                    ok = await conn.ping()
                    result[slug] = {
                        "connected": True,
                        "healthy": ok,
                        "server_info": conn.server_info,
                    }
                except Exception:
                    result[slug] = {"connected": True, "healthy": False}
            else:
                result[slug] = {"connected": False, "healthy": False}
        return result

    def get_status(self) -> Dict[str, Dict[str, Any]]:
        """获取所有连接状态 (同步, 不做 ping)"""
        result = {}
        for slug, conn in self._connections.items():
            result[slug] = {
                "connected": conn.is_connected,
                "transport": conn.config.transport,
                "server_info": conn.server_info if conn.is_connected else {},
                "tools_count": len(conn.config.discovered_tools) if conn.is_connected else 0,
            }
        return result
