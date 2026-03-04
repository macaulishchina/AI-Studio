#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -eq 0 ]]; then
  echo "请不要直接用 root 运行。请用普通用户执行此脚本，脚本内会按需调用 sudo。"
  exit 1
fi

if ! command -v sudo >/dev/null 2>&1; then
  echo "未检测到 sudo，无法继续。"
  exit 1
fi

if ! sudo -v; then
  echo "sudo 认证失败。"
  exit 1
fi

OS_ID=""
OS_VERSION_ID=""
if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  source /etc/os-release
  OS_ID="${ID:-}"
  OS_VERSION_ID="${VERSION_ID:-}"
fi

if [[ "${OS_ID}" != "ubuntu" ]]; then
  echo "当前系统不是 Ubuntu（检测到: ${OS_ID:-unknown}），脚本仍会尝试继续。"
fi

echo "[1/6] 安装基础依赖"
sudo apt-get update
sudo apt-get install -y curl ca-certificates gnupg lsb-release

echo "[2/6] 安装 Tailscale"
if ! command -v tailscale >/dev/null 2>&1; then
  curl -fsSL https://tailscale.com/install.sh | sh
else
  echo "tailscale 已安装，跳过。"
fi

echo "[3/6] 安装 xrdp"
sudo apt-get install -y xrdp xorgxrdp

# 允许 xrdp 使用证书
sudo adduser xrdp ssl-cert || true

# 为当前用户创建 xsession，优先使用 Ubuntu GNOME
if ! grep -q "gnome-session --session=ubuntu" "$HOME/.xsession" 2>/dev/null; then
  echo "gnome-session --session=ubuntu" > "$HOME/.xsession"
fi
chmod 644 "$HOME/.xsession"

echo "[4/6] 启用并启动服务"
sudo systemctl enable --now tailscaled
sudo systemctl enable --now xrdp
sudo systemctl restart xrdp

echo "[5/6] 配置防火墙（如果检测到 ufw）"
if command -v ufw >/dev/null 2>&1; then
  # 仅允许在 tailscale 虚拟网卡上访问 3389
  sudo ufw allow in on tailscale0 to any port 3389 proto tcp || true
  # 明确拒绝公网网卡上的 3389（规则可能已存在，允许失败）
  sudo ufw deny 3389/tcp || true
  echo "ufw 已写入规则（如 ufw 未启用，规则会在启用后生效）。"
else
  echo "未检测到 ufw，跳过防火墙配置。"
fi

echo "[6/6] 登录 Tailscale 并输出连接信息"
if sudo tailscale status >/dev/null 2>&1; then
  echo "Tailscale 已登录。"
else
  echo "即将执行 tailscale up（可能输出登录 URL，请在浏览器完成绑定）..."
  sudo tailscale up --accept-routes=true --accept-dns=true
fi

TAILSCALE_IP="$(tailscale ip -4 2>/dev/null | head -n1 || true)"

if [[ -n "${TAILSCALE_IP}" ]]; then
  echo ""
  echo "✅ 配置完成"
  echo "RDP 连接地址: ${TAILSCALE_IP}:3389"
  echo "用户名: $(whoami)"
  echo "密码: 该用户在 Ubuntu 的登录密码"
else
  echo ""
  echo "⚠️ 未获取到 Tailscale IPv4，请执行: tailscale ip -4"
fi

echo ""
echo "排查命令:"
echo "  systemctl status tailscaled xrdp --no-pager"
echo "  ss -lntp | grep 3389 || true"
echo "  sudo journalctl -u xrdp -n 50 --no-pager"
