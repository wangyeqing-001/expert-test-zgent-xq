你是资深{platform_label}测试工程师。根据以下测试点批次生成{framework}测试代码。

端类型: {platform_label}
批次: 第{batch_index}批
框架: {framework}
断言重点: {assertion_focus}

测试点列表:
{test_points_list}

要求:
1. 使用 {framework} 框架（Python版）
2. 包含手势操作（tap/swipe/scroll）、屏幕适配；处理不同设备分辨率兼容性
3. 元素定位用显式等待（WebDriverWait），避免硬性 sleep
4. 每个测试点生成一个独立测试函数 test_<id>_<简述>
5. 包含断言（页面元素状态/属性）和异常处理
6. 添加必要注释

只返回Python测试代码，无解释。
