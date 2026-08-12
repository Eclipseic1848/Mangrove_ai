from .settings import VLLM_CFG
from langchain_openai import ChatOpenAI
from .prompts import VOC_FILTER_PROMPT
from pydantic import BaseModel, Field
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from pydantic import ValidationError

class VocFilterInfo(BaseModel):
    analysis: str=Field(description="根据规则分析所给内容是否是有效数据的过程")
    result: str=Field(description="最终判断的结果，只能是 是或者否")

class VocFilterLLM(object):
    def __init__(self, model_source="vllm", temperature=0):
        self.temperature = temperature
        self.model = None
        self.model_source = model_source
        self.parser = PydanticOutputParser(pydantic_object=VocFilterInfo)
        self.load_model()

    def load_model(self):
        if self.model_source == "vllm":
            self._load_vllm_model()
            self.model_name = VLLM_CFG.MODEL_NAME



    def _load_vllm_model(self):
        print(f"\n🚀 从{VLLM_CFG.URL}加载主语言模型: {VLLM_CFG.MODEL_NAME}")

        enhanced_prompt = VOC_FILTER_PROMPT + "\n请严格按照格式要求输出JSON，不要包含任何额外的文本、解释或说明。"

        prompt = ChatPromptTemplate.from_messages([
            ("system", enhanced_prompt),
            ("human", "{instruction}\n\n{text}")
        ])

        self.model =  ChatOpenAI(model=VLLM_CFG.MODEL_NAME, temperature=0, base_url=VLLM_CFG.URL,api_key=VLLM_CFG.API_KEY)

        self.prompt = prompt.partial(instruction=self.parser.get_format_instructions())
        self.chain = self.prompt | self.model

    def get_response(self, user_prompt):
        if self.model_source == "vllm":
            return self._get_vllm_response(user_prompt)
        else:
            raise ValueError(f"不支持的 model_source: {self.model_source}")

    def parse_with_retry(self, text: str, max_retry: int = 2):
        for attempt in range(max_retry + 1):
            try:
                return self.parser.parse(text)
            except (ValidationError, Exception) as e:
                if attempt == max_retry:
                    raise
                text = self.model.invoke(
                    f"你刚才返回的内容无法通过 Pydantic 校验，错误如下：\n{e}\n"
                    f"请直接重新生成符合格式要求的 JSON，不要解释。"
                ).content
        return None

    def _get_vllm_response(self, user_prompt):
        """
        调用VLLM模型获取响应并解析

        Args:
            user_prompt: 用户输入的提示

        Returns:
            VocFilterInfo: 解析后的过滤信息对象
        """
        # 获取模型原始响应
        model_output = self.chain.invoke({"text": user_prompt})
        raw_output = model_output.content if hasattr(model_output, 'content') else str(model_output)
        print(f"模型输出: {raw_output}")

        # 尝试解析
        result = self.parse_with_retry(raw_output, max_retry=3)
        if result:
            return result

        # 解析失败时返回空结果
        return VocFilterInfo(
            analysis="",
            result="",
        )


