deploy_baoliang：这个是转为刷数据部署的，里面的progressResult是特殊化修改的，对于处理失败的，将直接用请求接口的输出html进行清理后返回给他。

new_deploy_wulan：是部署到乌兰察布的服务，里面的progressResult没有特殊化修改，对于处理失败的，就用提取的html重新处理一遍，这个是为了防止处理header的时候导致正文消失

zPosi这个主文件夹：同理wulan，里面的progressResult没有特殊化修改，对于处理失败的，就用提取的html重新处理一遍，这个是为了防止处理header的时候导致正文消失

总结一下：
new_deploy_wulan下的代码是在172.26.16.12服务器上给奇哥部署的，deploy_baoliang这个文件夹下的代码也是172.26.16.12上部署，为了保亮刷数据用，zPosi这个主文件夹下的代码是在爬虫端41上部署的，注意，乌兰察布和爬虫端的环境不一样，在爬虫端部署的代码，
必须用Tuple，然后还要from typing import Tuple，这是必须的！！！！而其他两个服务不要这个，也不要导包，就用小写的tuple就行了


