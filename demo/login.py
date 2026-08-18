"""示例登录模块"""

def login(username, password):
    """
    用户登录功能
    
    Args:
        username: 用户名
        password: 密码
    
    Returns:
        dict: 登录结果 {'success': bool, 'token': str}
    
    Raises:
        ValueError: 用户名或密码为空
        AuthenticationError: 认证失败
    """
    if not username or not password:
        raise ValueError("用户名和密码不能为空")
    
    # 模拟验证逻辑
    if username == "admin" and password == "123456":
        return {"success": True, "token": "abc123xyz"}
    else:
        raise AuthenticationError("用户名或密码错误")


class AuthenticationError(Exception):
    """认证异常"""
    pass
