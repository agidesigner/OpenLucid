FROM python:3.11-slim

WORKDIR /app

# [China] Uncomment the next 2 lines for faster builds in mainland China
# RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources
# ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-noto-cjk \
    fonts-noto-core \
    && rm -rf /var/lib/apt/lists/*
# fonts-noto-cjk gives the ffmpeg drawtext filter a font that can actually
# render CJK characters for the B-roll subtitle overlay. Without it, the
# compositor's ``drawtext=font=Noto Sans CJK SC`` falls through fontconfig
# to ffmpeg's built-in Latin-only font and every Chinese char renders as
# tofu boxes. The avatar provider burns its own subtitles server-side so
# that path was unaffected — only the B-roll section was broken.

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "120"]
