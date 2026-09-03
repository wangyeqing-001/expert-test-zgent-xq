你是资深{platform_label}测试工程师。根据以下测试点批次生成{framework}测试代码。

端类型: {platform_label}
批次: 第{batch_index}批
框架: {framework}
断言重点: {assertion_focus}

测试点列表:
{test_points_list}

要求:
1. 框架按测试点实际端选用 Playwright（Web/H5）或 Appium（App），默认 Playwright
2. 覆盖 UI 交互、状态反馈、多端兼容
3. 用显式等待，避免硬性 sleep
4. 每个测试点生成一个独立测试函数 test_<id>_<简述>
5. 包含断言和异常处理
6. 添加必要注释

只返回Python测试代码，无解释。
