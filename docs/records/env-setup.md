# 运行环境（uv）· 权威虚拟环境

> 本项目**唯一权威的 dev 环境 = 项目内 `.venv`**（随仓库走、可复现）。
> 旧的共享 `uv_a2a` 与 `rag_env` 均**停用**，不再往 `rag_env`（用户原 hello_world 环境）装任何东西。

## 权威环境位置

```
D:\17686\Desktop\A2A-project\.venv      （Python 3.10.20）
```

- 用 `uv venv` + `uv sync`（读 `pyproject.toml` / `uv.lock`）创建：a2a-sdk[http-server]==1.2.2、uvicorn 0.54、starlette 1.7、httpx 0.28.1、openai 3.26、python-dotenv、pyyaml + dev: pytest 8.3.4 / pytest-asyncio 0.25。
- 工单 02 额外增量装（用 `uv pip install --python ./.venv/Scripts/python.exe`，别 sync/prune）：
  `torch 2.9.1+cpu`（CPU，`version.cuda=None`）、`pymilvus 2.6.15`、`milvus-model 0.2.12`、`sentence-transformers 5.5.1`、`langchain-text-splitters 1.1.2`、`beautifulsoup4`、`markdownify`、`FlagEmbedding 1.4.0`。

## 装 CPU torch 的经验（踩过）

- 迁移不可行：`rag_env` 的 torch 是 **CUDA 版**（site-packages 下有 `nvidia/cudnn/triton`），不能当 CPU 版搬到别的 3.10 环境。
- `download.pytorch.org/whl/cpu` 直连慢；**Tuna 的 `pytorch-wheels/cpu` 路径 404**。可用：**SJTU 镜像 `https://mirror.sjtu.edu.cn/pytorch-wheels/cpu`** 或官方索引。cp310 上 CPU torch 最高 **2.9.1+cpu**（2.12.1+cpu 对 3.10 不存在）。
- 装法（aliyun 出依赖 + SJTU 出 torch，pin +cpu 防抓到 CUDA 版）：
  ```
  uv pip install --python ./.venv/Scripts/python.exe \
    --index-url https://mirrors.aliyun.com/pypi/simple/ \
    --extra-index-url https://mirror.sjtu.edu.cn/pytorch-wheels/cpu \
    --index-strategy unsafe-best-match "torch==2.9.1+cpu"
  ```
- `FlagEmbedding` 要**显式**装进 `.venv`（milvus-model 首用时懒装会装到错的解释器，导致 import 失败）。

## 工单 02 入库怎么跑（先归档 HTML，再入库）

```powershell
cd D:\17686\Desktop\A2A-project
$env:PYTHONPATH="src"; $env:PYTHONIOENCODING="utf-8"
# ① 抓 /docs → 把原始 HTML + Markdown 归档到 data/ragflow_docs/
.\.venv\Scripts\python.exe -u -m ingest run --crawl-only 2>&1 | Tee-Object logs\crawl.log
# ② 读归档语料 → 切分 → BGE-M3(CPU) → upsert 到 VM Milvus
.\.venv\Scripts\python.exe -u -m ingest run --reuse 2>&1 | Tee-Object logs\ingest.log
```

- Milvus 在 **VM**（`192.168.88.101:19530`，库 `a2a_cs_lzs` / 集合 `cs_kb_lzs`），嵌入模型在**宿主机** `.venv`。首次会 `create_database` + 建集合。
- 已实测：归档 100 篇 → 2770 子块 → md5 去重实存 **2662** 条；hybrid 检索命中正常。
- 日志坑：别用 `| tail`（管道缓冲，看不到实时进度）；直接写日志文件随时读，Win 下重定向要设 `PYTHONIOENCODING=utf-8` 否则中文乱码。
- `data/` 与 `logs/` 已在 `.gitignore` 内。

## 运行 / 测试

- 测试：`.\.venv\Scripts\python.exe -m pytest`（默认只跑无外部依赖快测；打真模型/VM 的用 `-m llm` / `-m e2e` opt-in）。
- IDE 解释器指到 `D:\17686\Desktop\A2A-project\.venv\Scripts\python.exe`。
