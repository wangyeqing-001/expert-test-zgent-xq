# 客户端测试配置示例

## Web测试 (Playwright)
WEB_TEST_URL=https://your-app.com
BROWSER=chromium

## 移动端测试 (Appium)
APPIUM_SERVER=http://localhost:4723/wd/hub
PLATFORM_NAME=Android
APP_PACKAGE=com.example.app
APP_ACTIVITY=.MainActivity

## API测试
API_BASE_URL=https://api.your-app.com/v1
API_TIMEOUT=10
