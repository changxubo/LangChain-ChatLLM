import os

os.system('pip install git+https://github.com/facebookresearch/detectron2.git')

import gradio as gr
import nltk
import sentence_transformers
import torch
from duckduckgo_search import ddg
from duckduckgo_search.utils import SESSION
from langchain.chains import RetrievalQA
from langchain.document_loaders import UnstructuredFileLoader
from langchain.embeddings.huggingface import HuggingFaceEmbeddings
from langchain.prompts import PromptTemplate
from langchain.prompts.prompt import PromptTemplate
from langchain.vectorstores import FAISS

from chatllm import ChatLLM
from chinese_text_splitter import ChineseTextSplitter

nltk.data.path.append('./nltk_data')

embedding_model_dict = {
    "ernie-tiny": "nghuyong/ernie-3.0-nano-zh",
    "ernie-base": "nghuyong/ernie-3.0-base-zh",
    "text2vec-base": "GanymedeNil/text2vec-base-chinese"
}

llm_model_dict = {
    "ChatGLM-6B-int8": "THUDM/chatglm-6b-int8",
    "ChatGLM-6B-int4": "THUDM/chatglm-6b-int4",
    "ChatGLM-6b-int4-qe": "THUDM/chatglm-6b-int4-qe",
    "Minimax": "Minimax"
}


DEVICE = "cuda" if torch.cuda.is_available(
) else "mps" if torch.backends.mps.is_available() else "cpu"

def search_web(query):

        SESSION.proxies = {
            "http": f"socks5h://localhost:7890",
            "https": f"socks5h://localhost:7890"
        }
        results = ddg(query)
        web_content = ''
        if results:
            for result in results:
                web_content += result['body']
        return web_content

def load_file(filepath):
    if filepath.lower().endswith(".pdf"):
        loader = UnstructuredFileLoader(filepath)
        textsplitter = ChineseTextSplitter(pdf=True)
        docs = loader.load_and_split(textsplitter)
    else:
        loader = UnstructuredFileLoader(filepath, mode="elements")
        textsplitter = ChineseTextSplitter(pdf=False)
        docs = loader.load_and_split(text_splitter=textsplitter)
    return docs



def init_knowledge_vector_store(embedding_model, filepath):
    embeddings = HuggingFaceEmbeddings(
        model_name=embedding_model_dict[embedding_model], )
    embeddings.client = sentence_transformers.SentenceTransformer(
        embeddings.model_name, device=DEVICE)

    docs = load_file(filepath)

    vector_store = FAISS.from_documents(docs, embeddings)
    return vector_store


def get_knowledge_based_answer(query,
                               large_language_model,
                               vector_store,
                               VECTOR_SEARCH_TOP_K,
                               web_content,
                               history_len,
                               temperature,
                               top_p,
                               chat_history=[]):
    if web_content:
        prompt_template = f"""基于以下已知信息，简洁和专业的来回答用户的问题。
                            如果无法从中得到答案，请说 "根据已知信息无法回答该问题" 或 "没有提供足够的相关信息"，不允许在答案中添加编造成分，答案请使用中文。
                            已知网络检索内容：{web_content}""" + """
                            已知内容:
                            {context}
                            问题:
                            {question}"""
    else:
        prompt_template = """基于以下已知信息，请简洁并专业地回答用户的问题。
            如果无法从中得到答案，请说 "根据已知信息无法回答该问题" 或 "没有提供足够的相关信息"。不允许在答案中添加编造成分。另外，答案请使用中文。

            已知内容:
            {context}

            问题:
            {question}"""
    prompt = PromptTemplate(template=prompt_template,
                            input_variables=["context", "question"])
    chatLLM = ChatLLM()
    chatLLM.history = chat_history[-history_len:] if history_len > 0 else []
    if large_language_model == "Minimax":
        chatLLM.model = 'Minimax'
    else:
        chatLLM.load_model(model_name_or_path=llm_model_dict[large_language_model])
        chatLLM.temperature = temperature
        chatLLM.top_p = top_p

    knowledge_chain = RetrievalQA.from_llm(
        llm=chatLLM,
        retriever=vector_store.as_retriever(
            search_kwargs={"k": VECTOR_SEARCH_TOP_K}),
        prompt=prompt)
    knowledge_chain.combine_documents_chain.document_prompt = PromptTemplate(
        input_variables=["page_content"], template="{page_content}")

    knowledge_chain.return_source_documents = True

    result = knowledge_chain({"query": query})
    return result


def clear_session():
    return '', None


def predict(input,
            large_language_model,
            embedding_model,
            file_obj,
            VECTOR_SEARCH_TOP_K,
            history_len,
            temperature,
            top_p,
            use_web,
            history=None):
    if history == None:
        history = []
    print(file_obj.name)
    vector_store = init_knowledge_vector_store(embedding_model, file_obj.name)
    if use_web == 'True':
        web_content = search_web(query=input)
    else:
        web_content = ''
    resp = get_knowledge_based_answer(
        query=input,
        large_language_model=large_language_model,
        vector_store=vector_store,
        VECTOR_SEARCH_TOP_K=VECTOR_SEARCH_TOP_K,
        web_content=web_content,
        chat_history=history,
        history_len=history_len,
        temperature=temperature,
        top_p=top_p,
    )
    print(resp)
    history.append((input, resp['result']))
    return '', history, history


if __name__ == "__main__":
    block = gr.Blocks()
    with block as demo:
        gr.Markdown("""<h1><center>LangChain-ChatLLM-Webui</center></h1>
        <center><font size=3>
        本项目基于LangChain和大型语言模型系列模型, 提供基于本地知识的自动问答应用. <br>
        目前项目提供基于<a href='https://github.com/THUDM/ChatGLM-6B' target="_blank">ChatGLM-6B </a>的LLM和包括GanymedeNil/text2vec-large-chinese、nghuyong/ernie-3.0-base-zh、nghuyong/ernie-3.0-nano-zh在内的多个Embedding模型, 支持上传 txt、docx、md等文本格式文件. <br>
        后续将提供更加多样化的LLM、Embedding和参数选项供用户尝试, 欢迎关注<a href='https://github.com/thomas-yanxin/LangChain-ChatGLM-Webui' target="_blank">Github地址</a>.
        </center></font>
        """)
        with gr.Row():
            with gr.Column(scale=1):
                model_choose = gr.Accordion("模型选择")
                with model_choose:
                    large_language_model = gr.Dropdown(
                        list(llm_model_dict.keys()),
                        label="large language model",
                        value="ChatGLM-6B-int4")

                    embedding_model = gr.Dropdown(list(embedding_model_dict.keys()),
                                                label="Embedding model",
                                                value="text2vec-base")

                file = gr.File(label='请上传知识库文件',
                               file_types=['.txt', '.md', '.docx'])
                
                use_web = gr.Radio(["True", "False"], label="Web Search",
                               value="False"
                               )
                model_argument = gr.Accordion("模型参数配置")

                with model_argument:

                    VECTOR_SEARCH_TOP_K = gr.Slider(1,
                                                    10,
                                                    value=6,
                                                    step=1,
                                                    label="vector search top k",
                                                    interactive=True)

                    HISTORY_LEN = gr.Slider(0,
                                            3,
                                            value=0,
                                            step=1,
                                            label="history len",
                                            interactive=True)

                    temperature = gr.Slider(0,
                                            1,
                                            value=0.01,
                                            step=0.01,
                                            label="temperature",
                                            interactive=True)
                    top_p = gr.Slider(0,
                                    1,
                                    value=0.9,
                                    step=0.1,
                                    label="top_p",
                                    interactive=True)
                

            with gr.Column(scale=4):
                chatbot = gr.Chatbot(label='ChatLLM').style(height=600)
                message = gr.Textbox(label='请输入问题')
                state = gr.State()

                with gr.Row():
                    clear_history = gr.Button("🧹 清除历史对话")
                    send = gr.Button("🚀 发送")

                    send.click(predict,
                               inputs=[
                                   message, large_language_model,
                                   embedding_model, file, VECTOR_SEARCH_TOP_K,
                                   HISTORY_LEN, temperature, top_p, use_web,state
                               ],
                               outputs=[message, chatbot, state])
                    clear_history.click(fn=clear_session,
                                        inputs=[],
                                        outputs=[chatbot, state],
                                        queue=False)

                    message.submit(predict,
                                   inputs=[
                                       message, large_language_model,
                                       embedding_model, file,
                                       VECTOR_SEARCH_TOP_K, HISTORY_LEN,
                                       temperature, top_p, use_web,state
                                   ],
                                   outputs=[message, chatbot, state])
        gr.Markdown("""提醒：<br>
        1. 使用时请先上传自己的知识文件，并且文件中不含某些特殊字符，否则将返回error. <br>
        2. 有任何使用问题，请通过[问题交流区](https://huggingface.co/spaces/thomas-yanxin/LangChain-ChatLLM/discussions)或[Github Issue区](https://github.com/thomas-yanxin/LangChain-ChatGLM-Webui/issues)进行反馈. <br>
        """)
    demo.queue().launch(server_name='0.0.0.0', share=False)
