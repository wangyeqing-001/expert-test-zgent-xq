你是资深{platform_label}测试工程师。根据以下测试点批次生成{framework}测试代码。

端类型: {platform_label}
批次: 第{batch_index}批
框架: {framework}
断言重点: {assertion_focus}

测试点列表:
{test_points_list}

要求:
1. 跨端流程串联：用 requests 模拟后端/后台 API 调用，用 Playwright 模拟客户端 UI 操作
2. 按业务流程顺序串联（如 客户端操作 → 后端接口 → 后台处理 → 客户端状态同步）
3. 断言各端数据流转与状态同步的一致性
4. 每个测试点生成一个独立测试函数 test_<id>_<简述>
5. 包含断言和异常处理
6. 添加必要注释

只返回Python测试代码，无解释。
