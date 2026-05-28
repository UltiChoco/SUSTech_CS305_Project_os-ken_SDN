# 环境搭建

## 前置条件

需要 Linux 环境（推荐使用虚拟机）。控制器依赖：

- Python 3.8（通过 conda 安装）
- Mininet 2.3.0
- os-ken

## 安装步骤

### 1. 安装 Mininet

根据你的平台选择对应的 [Mininet 安装指南](https://github.com/mininet/mininet/releases)。

验证安装：

```bash
sudo mn --test pingall
```

若输出类似以下内容，说明 Mininet 配置正确：

```
*** Creating network
*** Adding controller
*** Adding hosts: ...
*** Ping: testing ping reachability
h1 -> h2
h2 -> h1
*** Results: 0% dropped (2/2 received)
```

### 2. 通过 Miniconda 安装 Python 3.8

**amd64 用户：**

```bash
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
sh Miniconda3-latest-Linux-x86_64.sh -b -p ${HOME}/software/miniconda3
echo "export PATH=${HOME}/software/miniconda3/bin:\$PATH" >> ~/.bashrc
source ~/.bashrc
conda init bash
source ~/.bashrc
conda create -n cs305 python=3.8
conda activate cs305
```

**ARM（Apple Silicon）用户：**

```bash
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-aarch64.sh
# 后续步骤同上，包名替换为 aarch64 版本
```

### 3. 安装依赖

```bash
sudo apt install -y build-essential python3-dev libxml2-dev libxslt1-dev \
    zlib1g-dev pkg-config arping

pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 4. 验证安装

```bash
osken-manager --version
```
