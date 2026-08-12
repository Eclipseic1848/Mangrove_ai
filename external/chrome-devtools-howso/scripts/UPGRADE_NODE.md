# Node.js 升级完成

## 升级结果

✅ **Node.js 已成功升级**
- **旧版本**: v12.22.9
- **新版本**: v24.13.0 (LTS)
- **npm 版本**: v11.6.2

## 使用方法

### 方法 1: 在当前终端使用（临时）

```bash
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
```

### 方法 2: 自动加载（推荐）

nvm 已经配置在 `~/.bashrc` 中，**重新打开终端**后会自动加载。

如果当前终端还没有加载，运行：
```bash
source ~/.bashrc
```

### 验证安装

```bash
node --version  # 应该显示 v24.13.0
npm --version   # 应该显示 11.6.2
```

## 常用 nvm 命令

```bash
# 查看已安装的版本
nvm list

# 查看所有可用版本
nvm list-remote

# 安装特定版本
nvm install 20.19.0

# 切换到特定版本
nvm use 20.19.0

# 设置默认版本
nvm alias default 24.13.0

# 查看当前使用的版本
nvm current
```

## 编译测试

编译 chrome-devtools-mcp 项目：

```bash
cd chrome-devtools-mcp
npm run build
```

✅ **编译已成功通过！**

## 注意事项

1. **每次打开新终端时**，nvm 会自动加载（已配置在 ~/.bashrc）
2. **如果当前终端没有加载 nvm**，运行 `source ~/.bashrc` 或重新打开终端
3. **系统级的 Node.js**（/usr/bin/node）仍然是旧版本，但 nvm 管理的版本优先级更高
4. **项目编译**现在应该可以正常工作了
