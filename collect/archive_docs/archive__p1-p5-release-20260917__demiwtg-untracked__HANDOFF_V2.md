# 传输线·交接(2026-09-14 20:05 定稿,接替窗口开场白:"读 HANDOFF_V2.md 接手")

## 一、一句话现状
图片下载线(①~④+档2)全部完成:882.3 万张图/0.772TB 在 COS kb/blobs/,账本已双落位(COS+训练机 meta)。
**传输线(图→训练机本地)未完成**:约只有最早几十万张真正落湖,其余全丢在"空档案骗局"里(见§三)。

## 二、已完成资产(勿动)
- COS kb/blobs/:882.3 万图(多次对账无误)
- COS kb/:语料 21 分片 28GB + 账本三件(qid_images.jsonl.gz 1.1GB/roles/captions)
- 训练机 meta/:账本全家(fat/graph/qid_images/roles/captions)已就位
- 训练机 corpus/:语料 21 分片已同步
- 训练机 datasets/demiwtg/blobs/:旧资产 199 万张,**一字节未动**(从未被删)
- demiflow 平台升级(U1 活性层+U2 BatchMapOp+U4)已实现并全测通过(70/70)
- demiwtg-data 仓库:③~⑤ 全链代码 + tools/ 五件 + ops/ 发射器已提交推送

## 三、传输线事故链(简述,详见 logs/night_watch_2026-09-12_14.log)
**根因**:fleet 25 机的 cosfs 挂载因匿名限频进入负缓存恶性循环→所有 blob 文件 stat 失败→
tar 产出 10240 字节全零空档案→drip_ship2.sh 用管道末端退出码(ssh 的 RC=0)判"成功"→
80 多万块全标记 ok,实际零字节落湖。lake 侧 codex 清白,什么都没删。

## 四、修复三件套(必做,缺一不可)
1. **修挂载**:每台机干净卸挂 cosfs(杀僵尸→fusermount -uz→重挂→验证首文件可见)
   原始挂载参数:`cosfs lhcos-368f6-1256345599:/lhcos-data /lhcos-data -ourl=http://cos.ap-singapore.myqcloud.com -odbglevel=err -oallow_other -opublic_bucket=1 -oensure_diskfree=1024`
2. **修发射器**:drip_ship2.sh 加 `set -o pipefail`(tar 本地失败必须传染整管)+
   湖侧 tar 解完后 `find 新到目录 -newer 起始时间戳 | wc -l` 校验>0 才标记 done
3. **修路径**:DEST 从 `/yzp/.../demiwtg`(顶层,错误)改为 `/yzp/.../demiwtg/datasets/demiwtg/kb`
   (镜像 COS 的 kb/ 结构,账本 path 字段直接对应,与旧资产完全隔离)

## 五、修复后的重传操作序列
1. 清空所有机的 ~/shipdone/(旧标记指向错误路径)
2. 确认每机 ~/minichunks2/ 有全量 8824 块文件(m00000~m08823)
3. 修正 drip_ship2.sh(DEST+pipefail+验货)→ scp 到 24 机
4. 逐机重启 drips(launch_drip3.sh 模式,两段分离防 pkill 自匹配)
5. 前三个块完成时湖侧验货(算实到文件数),确认非零再放量
6. 预计 3~14 小时(匿名通道每机 ~20 文件/秒)

## 六、通道基建(已就位,可直接用)
- 7 条反向隧道(22022~22029),lake→coordinator,已验证端到端通
- 24 机有 ~/ship_key(反向到 coordinator 的 ssh 密钥)+ ~/lighthouse_key(lake 密钥)
- socat 网关 23023~23029(0.0.0.0,内网可达)但仅 22022~22029 在 coordinator 本地监听
- 匿名 COS 读限频 ~10 req/s/IP(硬墙),认证 API 才能破(需用户提供密钥,暂未提供)

## 七、红线(不动)
- 训练机 lake_sync 已停勿启;旧 blobs/pages/meta 旧四件不动;lake 侧 codex 的活动范围需协调
- COS kb/ 目录(语料+账本+blobs)永久不动
- 概念集 = EN∪ZH 有页面(782 万),不是双语门槛

## 八、湖侧两个 blobs 树的区别(别再混淆!)
| 路径 | 内容 | 状态 |
|---|---|---|
| `demiwtg/blobs/`(顶层) | 我错误写入的目的地 | 3 个残渣目录,基本空 |
| `datasets/demiwtg/blobs/` | 前朝旧资产 199 万张 | 一字节未动 ✓ |
| `datasets/demiwtg/kb/blobs/`(待建) | 正确目的地 | 尚不存在,需创建 |
