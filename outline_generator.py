"""
大纲生成模块
分析 PDF 并生成文档大纲
"""

import json
import base64
from pathlib import Path
import requests


class OutlineGenerator:
    """文档大纲生成器"""

    def __init__(self, config: dict, logger, output_base: Path):
        """
        初始化大纲生成器

        Args:
            config: 配置字典
            logger: 日志记录器实例
            output_base: 输出基础路径
        """
        self.config = config
        self.logger = logger
        self.output_base = output_base

    def generate_outline(self, pdf_path: str, output_paths: dict = None) -> dict:
        """
        生成文档大纲

        Args:
            pdf_path: PDF文件路径
            output_paths: 自定义输出路径字典（可选）

        Returns:
            文档大纲字典
        """
        self.logger.info("\n>>> 步骤1: 生成文档大纲...")

        # 确定outline路径
        if output_paths and 'outline' in output_paths:
            outline_path = output_paths['outline']
        else:
            outline_path = self.output_base / "cache/outline.json"
            outline_path.parent.mkdir(parents=True, exist_ok=True)

        # 如果已存在大纲，直接加载
        if Path(outline_path).exists():
            self.logger.info("发现已有大纲，直接加载...")
            with open(outline_path, 'r', encoding='utf-8') as f:
                outline = json.load(f)
            self.logger.success(f"大纲已加载: {len(outline.get('structure', []))} 个章节")
            return outline

        # 读取PDF并编码为base64
        self.logger.info(f"正在读取PDF: {pdf_path}")
        with open(pdf_path, 'rb') as f:
            pdf_data = base64.b64encode(f.read()).decode('utf-8')

        # 生成大纲的提示词
        prompt = """请分析这份PDF文档，生成JSON格式的文档大纲。

要求：
1. 识别文档类型（research_report/journal_article）
2. 提取章节结构（标题、页码范围）
3. 为每个章节生成简短摘要（50字内）和关键词
4. 提取文档中的专业术语并提供中文翻译

输出JSON格式：
{
  "document_type": "research_report/journal_article",
  "structure": [
    {
      "level": 1,
      "title": "章节标题",
      "pages": [起始页, 结束页],
      "summary": "章节摘要（50字内）",
      "keywords": ["关键词1", "关键词2"]
    }
  ],
  "glossary": {
    "英文术语": "中文翻译"
  }
}

请直接返回JSON，不要添加任何解释。"""

        # 调用API
        self.logger.info("正在调用API分析文档...")
        headers = {
            "Authorization": f"Bearer {self.config['api']['outline_api_key']}",
            "Content-Type": "application/json"
        }

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:application/pdf;base64,{pdf_data}"
                        }
                    }
                ]
            }
        ]

        payload = {
            "model": self.config['api']['outline_api_model'],
            "messages": messages,
            "temperature": self.config['api']['temperature'],
            "max_tokens": self.config['api']['max_tokens']
        }

        response = requests.post(
            f"{self.config['api']['outline_api_base_url']}/chat/completions",
            headers=headers,
            json=payload,
            timeout=self.config['api']['timeout'],
            verify=True,
            proxies={'http': None, 'https': None}  # 禁用代理
        )

        response.raise_for_status()
        result = response.json()
        response_text = result['choices'][0]['message']['content'].strip()

        # 解析JSON（移除可能的markdown代码块标记）
        if response_text.startswith("```"):
            # 移除代码块标记
            lines = response_text.split('\n')
            response_text = '\n'.join(lines[1:-1])

        outline = json.loads(response_text)

        # 保存大纲
        outline_path.parent.mkdir(parents=True, exist_ok=True)
        with open(outline_path, 'w', encoding='utf-8') as f:
            json.dump(outline, f, ensure_ascii=False, indent=2)

        self.logger.success(f"大纲已生成: {len(outline['structure'])} 个章节")
        self.logger.info(f"大纲已保存: {outline_path}")

        return outline
