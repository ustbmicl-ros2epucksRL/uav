独立真实对象存储部署入口

平台目录内执行（先安装 requirements.txt）：
python scripts/cabinet_object_service.py up --directory /srv/cabinet-objects --host objects.example.internal --port 9443 --mode compose

此命令使用固定 SeaweedFS 4.52，生成私有桶、仅 GetObject/PutObject 的前缀策略、独立平台凭据、TLS、持久化 Compose 配置，并运行真实 S3 功能/权限检查。首次原生二进制下载需联网；可用 --weed /opt/seaweedfs/weed 指定已审查的同版本二进制。Compose 镜像为官方 chrislusf/seaweedfs:4.52，可将生成的 compose.json 中 image 替换成组织审查后的镜像摘要。

没有 Docker 的本机验证：
python scripts/cabinet_object_service.py up --directory /tmp/cabinet-objects --port 19443 --mode native

原生模式只绑定 127.0.0.1，适合本机测试、独立主机内通过受控代理访问；Docker 模式只向宿主发布 HTTPS S3 端口，内部 filer/master/volume 端口不得额外公开。不要把原生模式的 -ip.bind 改为公网地址。

生成的 client/objects.json、client/access、client/secret、client/ca.pem 是平台所需材料。跨主机复制这四个文件到平台受控目录后，更新 JSON 内三个文件的绝对路径；私钥/账号文件权限保持 0600。不要复制 admin/ 或 server/ 到平台。设置 SENSE_CABINET_OBJECT_CONFIG 指向平台上的 objects.json。

详细上线、迁移、恢复和验收流程：platform/docs/真实对象存储部署与迁移验收.txt。
