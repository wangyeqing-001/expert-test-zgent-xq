你是资深客户端测试工程师。根据以下需求生成{framework}测试代码。

功能: {function_name}
场景类型: {scenario_type}
测试点: {test_points}
优先级: {priority}

要求:
1. 使用{framework}框架，**必须输出Python语法**（禁止TypeScript/JavaScript）
   - Playwright用Python sync API：`from playwright.sync_api import Page, expect`
   - 方法签名：`def test_xxx(page: Page):`
   - 用 `page.goto()` / `page.locator()` / `expect().to_xxx()`
2. 覆盖场景: {description}
3. 重点测试: {test_points}
4. 包含断言和异常处理
5. 添加必要的注释
6. **代码必须放在 ` ```python ` 标记中返回**

只返回测试代码，无解释。
