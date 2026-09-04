题库源文件在这个目录的 JSON 里。改完后在本仓库根目录执行 `npm run seed`，会展开并写入 PostgreSQL。接口从数据库读，只改 JSON 不 seed 不会生效。

## 文件

- `alibaba-topics.json`：通用口径主题（含「Java 转 Agent」专题）
- `alibaba-parents.json`：通用口径父题。每道父题在服务端只展开 2 道精选追问，不再批量生成第三层追问
- `ailian-bank.json`：爱连健康现网主题、自我介绍、父题和文档里的追问
- `candidate.json`：候选人背景（Java 转 Agent），用来改首页口径

公开面经整理的父题会带 `source.label` 和 `source.url`，页面展示出处链接。来源是候选人公开自述，不等同于招聘方官方题库。

## 线上

连接串用环境变量 `PG_URI`。改 JSON 后执行 `npm run seed`（会先展开 `alibaba-parents.json`）。

容器镜像会在构建时生成 `data/public-bank.json`；数据库为空时，服务启动会自动导入。
