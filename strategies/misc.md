# Misc 题攻击流程

## 分析阶段 (5-10min)

1. 解压附件: `unzip`/`tar xf`/`7z x`/`unrar`，识别所有文件
2. `file` 识别类型，`binwalk -e` 检查隐藏内容，`exiftool` 查看元数据
3. `strings` 提取可见字符串，`xxd` 查看十六进制
4. 识别特征方向 (见下方分类)
5. 发现 2+ 分析方向时，调 `branch.py spawn` 并行试探

## 解题阶段

- 逐个验证假设，工具不行就写 Python 脚本
- 多层嵌套 (压缩包套隐写套编码) 逐层剥
- 发现 flag 立即输出到 progress.md 的 Flags Found 段

## 隐写 (Steganography)

### 图片
- **PNG**: `zsteg` (LSB) / `pngcheck` / 提取 IDAT
- **JPEG**: `steghide extract` (需密码) / `exiftool` 注释
- **BMP/GIF**: LSB / 调色板隐写 / `stegsolve`
- **对比工具**: `stegsolve` 逐通道查看，或两图 XOR

### 音频
- **WAV**: spectrogram (Audacity/Sonic Visualiser 看频谱图隐藏信息) / LSB
- **MP3**: ID3 标签 / 附带数据

### 文档
- **PDF**: `pdftotext` / `binwalk` / 隐藏图层
- **Office**: 解压后看 XML / 宏代码 / 隐藏文本

## 流量分析

- **pcap/pcapng**: `tshark -r file.pcap -Y "http" -T fields -e http.request.uri` 提取 HTTP
- 提取文件: `tshark -r file.pcap --export-objects http,/tmp/output/`
- TCP 流重组: `tshark -z "follow,tcp,ascii,0" -r file.pcap`
- WiFi 握手: `aircrack-ng` 爆破
- USB 键盘流量: 提取 HID 数据还原按键

## 内存取证

- `volatility -f memory.dump imageinfo` 识别系统
- `volatility -f memory.dump --profile=xxx pslist` 进程
- `volatility -f memory.dump --profile=xxx filescan` 文件
- `volatility -f memory.dump --profile=xxx dumpfiles` 提取
- `volatility -f memory.dump --profile=xxx hashdump` 密码哈希

## 编码识别

- **Base64**: 末尾 `=` / `A-Za-z0-9+/`
- **Base32**: `A-Z2-7=`
- **Hex**: `0-9a-f`
- **Brainfuck**: `><+-.,[]`
- **JSFuck**: `[]()!+`
- **Morse**: `.-/ `
- **Ook!**: `Ook. Ook! Ook?`
- **URL 编码**: `%XX`

## 压缩包技巧

- **伪加密**: ZIP 的 general purpose bit flag 改为 1 表示加密，改回 0 即可
- **密码爆破**: `fcrackzip` / `john` (注意 CTF 一般不是纯暴力)
- **注释隐藏**: `unzip -l` 看注释，或 `7z l -slt`
- **修复损坏**: `zip -FF` 修复 / 手动改 hex 修复文件头
