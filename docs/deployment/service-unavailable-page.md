# 网关故障提示页

适用已使用 Nginx 反向代理的 Linux 部署。仅调整网关错误提示，不改变登录、任务和接口权限。

在工程目录以普通部署账号运行（自检命令不要加 sudo），先安装静态页面，再加载引用它的 Nginx 配置：

```bash
sudo install -d -m 0755 /var/www/mangrove-errors
sudo install -m 0644 scripts/service-unavailable.html /var/www/mangrove-errors/service-unavailable.html
sudo install -m 0644 scripts/service-unavailable.json /var/www/mangrove-errors/service-unavailable.json
python3 scripts/check_nginx_error_page.py
```

检查脚本使用临时目录、独立 Nginx 进程和本机随机端口，不停止线上服务。可追加 `--config /实际/nginx/入口.conf` 验证已安装入口。成功后，在公网端口实际映射到的 Mangrove `server` 块加入以下内容。公网端口不一定映射到服务器的 80 端口；须核对 NAT 和实际连接，覆盖所有产品入口。修改前备份实际配置文件；不要覆盖其他站点或私网模型 Relay。

```nginx
server_tokens off;
error_page 502 503 504 /__mangrove_service_unavailable.html;

location = /__mangrove_service_unavailable.html {
    internal;
    alias /var/www/mangrove-errors/service-unavailable.html;
    charset utf-8;
    add_header Cache-Control "no-store" always;
}

location = /__mangrove_api_unavailable.json {
    internal;
    alias /var/www/mangrove-errors/service-unavailable.json;
    default_type application/json;
    add_header Cache-Control "no-store" always;
}
```

页面代理 `location /` 使用 `proxy_intercept_errors on;` 展示维护页。独立的 `location ^~ /api/` 须复制现有代理头、长超时和流式配置，设置 `proxy_intercept_errors off;` 与 `error_page 502 503 504 /__mangrove_api_unavailable.json;`：应用返回的 JSON 错误原样保留，网关断连或超时也保持原状态码并返回 JSON。新部署可使用 `scripts/nginx-http.conf` 完整模板，修改实际监听端口、IP 和应用端口。

```bash
sudo nginx -t && sudo systemctl reload nginx
```

配置检查失败时不要重载，恢复备份后重新检查。生效后，页面展示“系统正在维护升级中，请稍后重试”，HTTP 状态仍为 502、503 或 504，不会伪装成功；“重试”只重新打开当前页面。该提示不代表系统已经自动修复故障，持续无法访问时须检查后台服务。不要为演示而停止线上应用。

回退时先恢复 Nginx 配置并检查、重载，再考虑移除不再引用的 HTML。既有账号、配置和任务数据不受影响。
