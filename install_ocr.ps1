# 安装 Windows 中文(简体) OCR 语言功能 —— 需要以管理员身份运行
$ErrorActionPreference = 'Stop'
$cap = 'Language.OCR~~~zh-CN~0.0.1.0'
Write-Host ''
Write-Host '=== 正在安装 Windows 中文 OCR（光学字符识别）==='
Write-Host "    能力包: $cap"
Write-Host '    需要联网，通常几十 MB，请等它跑完…'
Write-Host ''
try {
    $r = Add-WindowsCapability -Online -Name $cap
    Write-Host ''
    Write-Host ("结果: " + $r.RestartNeeded + " / " + $r.Online)
    if ($r.RestartNeeded) { Write-Host '提示: 需要重启电脑后才完全生效。' }
} catch {
    Write-Host ''
    Write-Host ('安装失败: ' + $_.Exception.Message) -ForegroundColor Red
    Write-Host '可以改用手动方式：设置 → 时间和语言 → 语言和区域 → 中文(简体) → 语言选项 → 光学字符识别'
}
Write-Host ''
Write-Host '3 秒后自动关闭…'
Start-Sleep -Seconds 3
