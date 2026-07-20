# Union3 v4 PDF 重新固定记录（2026-07-20）

## 结论

arXiv 在不改变论文编号 `2311.12098v4` 的情况下重新生成了 PDF 容器。Standard
Astro 没有忽略哈希错误，而是先停止读取，再比较新旧文件。确认正文的确定性文本投影完全
一致后，Registry 才改为固定当前官方 PDF。

这次变化不改变 Table 9 的内容，也不改变科学合同。

## 可复核记录

| 项目 | 旧 PDF | 2026-07-20 下载的当前 PDF |
|---|---:|---:|
| 文件大小 | 5,982,644 bytes | 5,953,972 bytes |
| PDF SHA-256 | `abe164dd038ee02c7664f5b3e78eef6cf1bea65315711926d74a1456bba38040` | `6a8fccccecfc083d24c07f508d15ba273ebec1333fe4702d226520ffbaa603c9` |
| `pdftotext -layout -enc UTF-8` 输出 SHA-256 | `adad06c7c2121c52ae5a873db1a6bd6b52e216cce705df4fedf69be327b0ea14` | `adad06c7c2121c52ae5a873db1a6bd6b52e216cce705df4fedf69be327b0ea14` |

当前官方响应报告的 `Last-Modified` 为 `2025-06-23 01:48:25 GMT`。响应中的
ETag 不能替代实际下载文件的 SHA-256，因此 Registry 使用本地对完整响应体计算得到的
`6a8f…03c9`。

## 科学内容复核

Reader 从当前 PDF 得到：

```text
Section 5.3
PDF page label 58
Table 9
Flat ΛCDM / SNe
Ωm = 0.356 +0.028/-0.026
frequentist profile-χ², Δχ² = 1
```

新旧 PDF 的完整文本投影逐字节相同。Table 9 的四个 source anchor、候选主张和统计语义
因此保持不变；由于 source document hash 包含 PDF 哈希，新下载产生新的、不可混用的
source identity。

## 完整来源链

Reader 现在按固定顺序获取并记录三个对象：

1. `https://export.arxiv.org/api/query?id_list=2311.12098v4` 的 Atom 元数据；
2. `https://export.arxiv.org/e-print/2311.12098v4` 的精确源码包；
3. `https://arxiv.org/pdf/2311.12098v4` 的精确 PDF。

源码包的实际响应体为 5,414,745 bytes，SHA-256 为
`13d14b96ba72b0a548642c7d9e7c7cf6000de062cbc0dbe17bf30198ba1e1189`。
安全检查在内存中读取 tar 目录，不把文件解压到磁盘。当前包包含 40 个普通文件，声明的
未压缩总大小为 6,295,735 bytes；成员清单 SHA-256 为
`40b6f1edf7e8f2df31896221446a884ea9308b97e8c3c5c493caa41f5877a40f`。

Atom 响应体可能包含 feed 级生成信息，因此系统不把一个历史 Atom 字节哈希冒充成永久
Registry 常量。每次读取都先验证 entry ID 和 PDF link 精确指向 `2311.12098v4`，再对当次
完整响应体计算 SHA-256，并以该哈希作为对象存储键。源码包和 PDF 则必须匹配上述固定
哈希。HTML 只能辅助显示，不参与 Table 9 证据提取。

Table 9 的 anchor 仍由权威 PDF 驱动，`source_document_hash` 的既有科学语义没有改变；
Atom 与源码包哈希作为额外 acquisition provenance 保存在 SourceDocument 中。

## 安全规则

- 以后如果官方 PDF 的字节再次变化，Reader 仍然 `fail closed`。
- 不允许为了让下载成功而关闭 checksum。
- 必须重新比较正文、Table 9、方法描述和定位信息后，才能再次更新固定值。
- Atom、源码包和 PDF 单响应均不得超过 100 MB；源码包最多 10,000 个成员、声明的
  未压缩总大小不得超过 500 MB，并拒绝路径穿越、链接和特殊文件。
- 历史 Evidence Pack 继续保留当时使用的旧哈希，不改写历史记录。
