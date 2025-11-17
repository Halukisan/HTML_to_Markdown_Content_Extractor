# XpathGet
自研正文定位算法，可实现定位页面正文内容，并将页面内容清理无关标签后转化为MD格式。HTML转MD

1. 创建虚拟环境，按照requirement要求安装依赖（可能写的不全，自己看一下，beautifulsoup和....某个忘记写了）
2. 运行zGetContentByXpath.py文件`python zGetContentByXpath.py api`
3. 然后按照以下代码进行解析

```python
import requests

with open("htmlcontent.html", 'r', encoding='utf-8') as f:
    html_content = f.read()

response = requests.post(
    "http://localhost:8000/extract",
    json={"html_content": html_content}
)

print("Status Code:", response.status_code)
print("Response Text:", response.text)

if response.status_code == 200:
    try:
        result = response.json()
        markdown_content = result.get("markdown_content", response.text)  
    except ValueError:
        markdown_content = response.text

    with open("htmlcontent.md", 'w', encoding='utf-8') as f:
        f.write(markdown_content)
    print("Markdown content saved to htmlcontent.md")
else:
    print("Request failed!")
```
4. 对于服务端的部署：你需要上传wwebdriver_pool.py文件和zGetContentByXpath.py和requirement_updated.txt三个文件，记得创建虚拟环境
5. 使用` nohup gunicorn -k uvicorn.workers.UvicornWorker zGetContentByXpath:app --bind 0.0.0.0:8000 --workers 4 > gunicorn.log 2>&1 &`启动api服务
6. 使用`ps aux | grep gunicorn`查看运行情况

> 算法已经非常稳健，切勿修改任何一个数字和字符，每一个分值的计算和确定都是大量的测试数据得到的经验。

## API 端点
**xxxxxxxx:8000/extract** 
### 1. 健康检查

**GET** `/health`

检查API服务状态。

**响应示例:**
```json
{
  "status": "healthy",
  "timestamp": "2024-01-15T10:30:00"
}
```

### 2. 提取内容

**POST** `/extract`

从HTML内容中提取正文并转换为Markdown。

**请求体:**
```json
{
  "html_content": "<html>...</html>"
}
```

**响应体:**
```json
{
  "markdown_content": "# 标题\n\n这是正文内容...",
  "xpath": "//div[@class='content']",
  "status": "success"
}
```

### 代码示例

![alt text](image.png)

### 使用步骤：
> 由于是生产环境部署，当前版本a634196的提交修改了日志配置，减少IO，因此几乎看不到详细信息，如果要进行调试，请使用上一提交版本的code

1. 先进入虚拟环境，要是误删除了虚拟环境文件，就按照requirement_updated重新安装，但requirement_updated里面可能少写了一个或者两个包，自己去尝试

```shell
. venv/bin/activate 
```
2. 看看配置文件里面的是否符合你的要求

3. 然后进行部署
对于高并发的情况：
```shell
chmod +x deploy.sh
./deploy.sh
```
对于并发小的情况：

```shell
nohup gunicorn -k uvicorn.workers.UvicornWorker zGetContentByXpath:app --bind 0.0.0.0:8000 --workers 4 > gunicorn.log 2>&1 &
```

4. 查看运行情况
```shell
chmod +x monitor.sh
./monitor.sh
```
或
```shell
ps aux | grep gunicorn
```
5. 关闭运行

```
kill xxxx
```
```
