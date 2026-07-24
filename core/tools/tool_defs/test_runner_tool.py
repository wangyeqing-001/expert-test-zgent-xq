"""测试运行工具"""
import subprocess


def test_runner_tool(test_file: str) -> dict:
    """测试运行工具"""
    try:
        result = subprocess.run(
            ['pytest', test_file, '-v'],
            capture_output=True,
            text=True,
            timeout=60
        )
        
        return {
            'success': result.returncode == 0,
            'output': result.stdout,
            'errors': result.stderr
        }
    except Exception as e:
        return {
            'success': False,
            'output': '',
            'errors': str(e)
        }
