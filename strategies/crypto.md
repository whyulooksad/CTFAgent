# Crypto 题攻击流程

## 分析阶段 (5-10min)

1. 解压附件: `unzip`/`tar xf`/`7z x`，识别所有文件
2. `file` 识别文件类型，`cat`/`xxd` 查看内容
3. 识别密码学特征 (见下方分类)
4. 发现 2+ 分析方向时，调 `branch.py spawn` 并行试探

## 解题阶段

- 用 Python 写解密脚本 (pycryptodome/gmpy2/sympy 按需 pip install)
- 已知攻击方法直接实现，不要犹豫
- 参数异常优先查对应攻击方法

## RSA

- **小公钥指数** (e=3): 立方根攻击，c^(1/3) 直接开方
- **Wiener 攻击**: 私钥 d 过小 (e 很大，N 很大)，连分数逼近
- **共模攻击**: 同一明文用相同 N 不同 e 加密，扩展欧几里得
- **低私钥指数**: d 较小时 Boneh-Durfee
- **分解 N**: 查 factordb / Fermat 分解 (p/q 接近) / yafu / Pollard's p-1
- **Coppersmith**: 部分已知明文高位/低位，small roots
- **选择密文攻击**: 有解密 oracle 时

## AES

- **ECB**: 相同明文块产生相同密文块，分组攻击
- **CBC**: Padding Oracle (PKCS#7) / 字节翻转攻击
- **密钥提取**: 弱密钥/已知密钥/侧信道
- **模式混淆**: ECB 当 CBC 用，IV 可控

## 古典密码

- **凯撒**: 遍历 25 种位移
- **维吉尼亚**: Kasiski 测试 / Index of Coincidence 求密钥长度
- **替换密码**: 频率分析
- **仿射**: 穷举 a/b
- **培根/栅栏**: 识别特征

## 哈希

- 识别类型 (MD5/SHA1/SHA256)，查彩虹表
- 长度扩展攻击 (MD5/SHA1)
- 碰撞构造

## 其他

- **LCG**: 线性同余，已知输出恢复参数
- **椭圆曲线**: 异常曲线 / Smart's attack / Pohlig-Hellman
- **格密码**: LLL 算法
- **一次性密码本**: 密钥复用 XOR
