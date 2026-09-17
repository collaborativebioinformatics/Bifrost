FROM quay.io/condaforge/miniforge3:26.7.2-0

ENV TZ=Europe
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /tmp
COPY . .
RUN pip install --no-cache-dir . && \
    conda clean --all --yes && \
    rm -rf /tmp/*

WORKDIR /home