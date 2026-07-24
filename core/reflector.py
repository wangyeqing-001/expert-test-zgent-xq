"""反思校验器 - 评估结果质量"""
from typing import Dict, List


class Reflector:
    """Agent反思模块"""
    
    def __init__(self):
        self.evaluation_log = []
    
    def evaluate(self, task_result: Dict, criteria: List[str] = None) -> Dict:
        """
        评估任务结果
        :param task_result: 任务执行结果
        :param criteria: 评估标准列表
        :return: 评估报告
        """
        if criteria is None:
            criteria = ['completeness', 'correctness', 'quality']
        
        report = {
            'passed': True,
            'scores': {},
            'issues': [],
            'suggestions': []
        }
        
        # 1. 完整性检查
        if 'completeness' in criteria:
            completeness_score = self._check_completeness(task_result)
            report['scores']['completeness'] = completeness_score
            if completeness_score < 0.8:
                report['passed'] = False
                report['issues'].append('结果不完整')
                report['suggestions'].append('补充缺失的测试场景')
        
        # 2. 正确性检查
        if 'correctness' in criteria:
            correctness_score = self._check_correctness(task_result)
            report['scores']['correctness'] = correctness_score
            if correctness_score < 0.7:
                report['passed'] = False
                report['issues'].append('存在语法或逻辑错误')
                report['suggestions'].append('修复代码错误')
        
        # 3. 质量检查
        if 'quality' in criteria:
            quality_score = self._check_quality(task_result)
            report['scores']['quality'] = quality_score
            if quality_score < 0.6:
                report['suggestions'].append('优化测试用例覆盖度')
        
        # 记录评估日志
        self.evaluation_log.append(report)
        
        return report
    
    def _check_completeness(self, result: Dict) -> float:
        """检查完整性"""
        # 简单启发式：检查关键字段
        required_fields = ['test_code', 'function_name']
        present = sum(1 for field in required_fields if field in result)
        
        return present / len(required_fields)
    
    def _check_correctness(self, result: Dict) -> float:
        """检查正确性"""
        test_code = result.get('test_code', '')
        
        if not test_code:
            return 0.0
        
        # 基本语法检查
        issues = 0
        
        # 检查是否有import
        if 'import' not in test_code:
            issues += 1
        
        # 检查是否有断言
        if 'assert' not in test_code and 'expect' not in test_code:
            issues += 1
        
        # 检查是否有函数定义
        if 'def test_' not in test_code:
            issues += 1
        
        # 返回正确性分数（无问题=1.0）
        return max(0.0, 1.0 - (issues * 0.3))
    
    def _check_quality(self, result: Dict) -> float:
        """检查质量"""
        test_code = result.get('test_code', '')
        
        if not test_code:
            return 0.0
        
        quality_indicators = 0
        
        # 有注释
        if '"""' in test_code or '#' in test_code:
            quality_indicators += 1
        
        # 有多个测试函数
        if test_code.count('def test_') > 1:
            quality_indicators += 1
        
        # 有异常处理
        if 'try:' in test_code or 'except' in test_code:
            quality_indicators += 1
        
        # 有mock使用
        if 'mock' in test_code.lower() or 'patch' in test_code:
            quality_indicators += 1
        
        return quality_indicators / 4.0
    
    def get_summary(self) -> Dict:
        """获取评估摘要"""
        if not self.evaluation_log:
            return {'total_evaluations': 0}
        
        return {
            'total_evaluations': len(self.evaluation_log),
            'pass_rate': sum(1 for log in self.evaluation_log if log['passed']) / len(self.evaluation_log),
            'avg_scores': self._calculate_avg_scores()
        }
    
    def _calculate_avg_scores(self) -> Dict[str, float]:
        """计算平均分数"""
        if not self.evaluation_log:
            return {}
        
        all_scores = {}
        for log in self.evaluation_log:
            for key, value in log.get('scores', {}).items():
                if key not in all_scores:
                    all_scores[key] = []
                all_scores[key].append(value)
        
        return {
            key: sum(scores) / len(scores)
            for key, scores in all_scores.items()
        }
