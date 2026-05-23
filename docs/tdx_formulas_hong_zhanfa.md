# 洪战法 × 通达信条件选股公式

> 与 `strategy_hong_zhanfa.md`（量化实现版）对齐。在通达信中：**功能 → 选股器 → 条件选股 → 其它类型 → 新建条件**，将下列公式粘贴到对应公式组；或建为 **.tni** 条件文件导入。
> **使用前必读**：下文 **RPS** 依赖扩展数据 `EXTDATA_USER(6)`、`EXTDATA_USER(7)`，须与 `tdx_rps_extension_zhutu.md` / `learn/tdx_taoboshi_reference.md` 中配置一致（一般为 120 日、250 日 RPS，且数值需 `/10` 得到 0–100）。

---

## 一、共用中间定义（建议单独建「洪战法_公共」条件，其它公式用 `#INCLUDE` 或手工合并）

若你的通达信版本**不支持** `#INCLUDE`，请把下面 `{共用}` 整段复制到每个选股公式**最前面**（融合公式已内置一份完整版）。

```text
{======== 洪战法-共用（RPS / RSI / MACD / 低位风控）========}
HC120:=EXTDATA_USER(6,0);
HC250:=EXTDATA_USER(7,0);
RPS120:=HC120/10;
RPS250:=HC250/10;

{--- RSI(14) ---}
LC:=REF(C,1);
RSI14:=SMA(MAX(C-LC,0),14,1)/SMA(ABS(C-LC),14,1)*100;

{--- MACD：DIF / DEA / 柱 ---}
DIF:=EMA(C,12)-EMA(C,26);
DEA:=EMA(DIF,9);
MACD柱:=(DIF-DEA)*2;

{--- 均线 ---}
MA5:=MA(C,5);
MA10:=MA(C,10);
MA20:=MA(C,20);
MA30:=MA(C,30);
MA60:=MA(C,60);
MA120:=MA(C,120);
MA250:=MA(C,250);

{--- 通用风控：未翻倍 + RPS 强度（文档：RPS120≥80 或 RPS250≥80）---}
未翻倍:=C<LLV(L,250)*2;
RPS强:=RPS120>=80 OR RPS250>=80;
HK风控:=未翻倍 AND RPS强;

{--- 流动性（可选，与火车轨类似，防极小盘）---}
流动性:=VOL>0 AND FROMOPEN>0;
{换手率<15%，可按需改为 10 或删除}
换手限:=VOL/CAPITAL*100<15;
```

**说明**

| 项 | 说明 |
|----|------|
| 行业 RPS ≥ 70 | 通达信需单独做**板块扩展数据**或板块排序，本公式**未内置**，选股后可用板块强度人工筛。 |
| RPS(50) | 若未做 50 日扩展数据，用 **RPS120/RPS250** 代替（上表已体现）。 |
| ST / 退市 | 可在选股界面勾选「剔除 ST」，或加：`NOT(NAMELIKE('ST'))`（名称规则因版本略有差异）。 |

---

## 二、朝天椒战法（均线粘合 + 一阳穿多线 + 倍量）

**逻辑摘要**：涨幅≥5%；实体/振幅>60%；上影≤实体一半；量≥昨2倍且≥5日均量1.5倍且≤5倍；收盘在 MA5/10/20/30 上至少 3 条；四线粘合≤3%；收盘>MA60；(MA60>MA120 或 MA60 近 5 日抬高)。

```text
{======== 洪战法-朝天椒 =========}
LC:=REF(C,1);
HC120:=EXTDATA_USER(6,0);
HC250:=EXTDATA_USER(7,0);
RPS120:=HC120/10;
RPS250:=HC250/10;
未翻倍:=C<LLV(L,250)*2;
RPS强:=RPS120>=80 OR RPS250>=80;

MA5:=MA(C,5);
MA10:=MA(C,10);
MA20:=MA(C,20);
MA30:=MA(C,30);
MA60:=MA(C,60);
MA120:=MA(C,120);

涨幅:=(C-REF(C,1))/REF(C,1);
实体:=ABS(C-O);
振幅:=H-L;
实体比:=实体/(振幅+0.001);
上影:=H-MAX(C,O);

倍量:=V>=REF(V,1)*2 AND V>=MA(V,5)*1.5 AND V<=MA(V,5)*5;

N1:=IF(C>MA5,1,0);
N2:=IF(C>MA10,1,0);
N3:=IF(C>MA20,1,0);
N4:=IF(C>MA30,1,0);
穿线数:=N1+N2+N3+N4;

MMAX:=MAX(MAX(MA5,MA10),MAX(MA20,MA30));
MMIN:=MIN(MIN(MA5,MA10),MIN(MA20,MA30));
粘合:=(MMAX-MMIN)/C<=0.03;

趋势位:=C>MA60 AND (MA60>MA120 OR MA60>REF(MA60,5));

K形:=涨幅>=0.05 AND 实体比>0.6 AND 上影<=实体*0.5;

朝天椒:=K形 AND 倍量 AND 穿线数>=3 AND 粘合 AND 趋势位 AND 未翻倍 AND RPS强;

朝天椒;
```

---

## 三、大阳共振一飞冲天（多头 + 放量 + 新高/突破 + MACD）

**逻辑摘要**：涨幅≥5%；实体/振幅>50%；上影≤实体/3；成交量在 5 日均量 1.5～3 倍；MA5>MA10>MA20>MA60 且 C>MA60；**（创 20 日新高 或 昨收在 MA60 下今日站上）**；DIF>0、DEA>0、DIF>DEA、红柱放大。

*小平台横盘*在公式里展开过长，此处用 **HHV(H,20)** 突破 + **均线突破** 二选一近似；需严格小平台请在股票池二次筛选。

```text
{======== 洪战法-大阳共振 =========}
LC:=REF(C,1);
HC120:=EXTDATA_USER(6,0);
HC250:=EXTDATA_USER(7,0);
RPS120:=HC120/10;
RPS250:=HC250/10;
未翻倍:=C<LLV(L,250)*2;
RPS强:=RPS120>=80 OR RPS250>=80;

DIF:=EMA(C,12)-EMA(C,26);
DEA:=EMA(DIF,9);
MACD柱:=(DIF-DEA)*2;

MA5:=MA(C,5);
MA10:=MA(C,10);
MA20:=MA(C,20);
MA60:=MA(C,60);

涨幅:=(C-REF(C,1))/REF(C,1);
实体:=ABS(C-O);
振幅:=H-L;
实体比:=实体/(振幅+0.001);
上影:=H-MAX(C,O);

量能:=V>=MA(V,5)*1.5 AND V<=MA(V,5)*3;

多头:=MA5>MA10 AND MA10>MA20 AND MA20>MA60 AND C>MA60;

新高突破:=C>=HHV(H,20);
均线突破:=REF(C,1)<MA60 AND C>MA60;
突破有效:=新高突破 OR 均线突破;

大阳K:=涨幅>=0.05 AND 实体比>0.5 AND 上影<=实体/3;

MACD确认:=DIF>0 AND DEA>0 AND DIF>DEA AND MACD柱>REF(MACD柱,1);

大阳共振:=大阳K AND 量能 AND 多头 AND 突破有效 AND MACD确认 AND 未翻倍 AND RPS强;

大阳共振;
```

---

## 四、中线趋势战法（回踩 MA20 + 多头年线 + RSI/MACD）

**逻辑摘要**：MA20>MA60>MA250；MA250≥REF(MA250,20)；收盘在 MA20～MA20×1.02 之间且收阳；RSI14>50 且 RSI 上升；DIF>DEA 且红柱>0。  
（文档中「涨跌日量比」等从略，避免公式超限，可在 Python 端 `hong_zhanfa` 模块精筛。）

```text
{======== 洪战法-中线趋势 =========}
LC:=REF(C,1);
HC120:=EXTDATA_USER(6,0);
HC250:=EXTDATA_USER(7,0);
RPS120:=HC120/10;
RPS250:=HC250/10;
未翻倍:=C<LLV(L,250)*2;
RPS强:=RPS120>=80 OR RPS250>=80;

RSI14:=SMA(MAX(C-LC,0),14,1)/SMA(ABS(C-LC),14,1)*100;

DIF:=EMA(C,12)-EMA(C,26);
DEA:=EMA(DIF,9);
MACD柱:=(DIF-DEA)*2;

MA20:=MA(C,20);
MA60:=MA(C,60);
MA250:=MA(C,250);

趋势锚:=MA20>MA60 AND MA60>MA250 AND MA250>=REF(MA250,20);
价位:=C>MA20 AND C<=MA20*1.02 AND C>O;

动量:=RSI14>50 AND RSI14>REF(RSI14,1) AND DIF>DEA AND MACD柱>0;

中线:=趋势锚 AND 价位 AND 动量 AND 未翻倍 AND RPS强;

中线;
```

---

## 五、融合选股（三套满足其一 + 通用风控）

用于 **每日一遍**：朝天椒 **OR** 大阳共振 **OR** 中线，且统一带 **未翻倍 + RPS**。若报「公式过长」，可拆成两个条件：条件 A=`HK_OR`，条件 B=`HK风控`，在选股器里 **交集**（或把本段拆文件）。

```text
{======== 洪战法-融合（OR 三套）========}
LC:=REF(C,1);
HC120:=EXTDATA_USER(6,0);
HC250:=EXTDATA_USER(7,0);
RPS120:=HC120/10;
RPS250:=HC250/10;
未翻倍:=C<LLV(L,250)*2;
RPS强:=RPS120>=80 OR RPS250>=80;
HK风控:=未翻倍 AND RPS强;

DIF:=EMA(C,12)-EMA(C,26);
DEA:=EMA(DIF,9);
MACD柱:=(DIF-DEA)*2;
RSI14:=SMA(MAX(C-LC,0),14,1)/SMA(ABS(C-LC),14,1)*100;

MA5:=MA(C,5);
MA10:=MA(C,10);
MA20:=MA(C,20);
MA30:=MA(C,30);
MA60:=MA(C,60);
MA120:=MA(C,120);
MA250:=MA(C,250);

{--- 朝天椒 ---}
涨幅1:=(C-REF(C,1))/REF(C,1);
实体1:=ABS(C-O);
振幅1:=H-L;
实体比1:=实体1/(振幅1+0.001);
上影1:=H-MAX(C,O);
倍量1:=V>=REF(V,1)*2 AND V>=MA(V,5)*1.5 AND V<=MA(V,5)*5;
N1:=IF(C>MA5,1,0);
N2:=IF(C>MA10,1,0);
N3:=IF(C>MA20,1,0);
N4:=IF(C>MA30,1,0);
穿线数:=N1+N2+N3+N4;
MMAX:=MAX(MAX(MA5,MA10),MAX(MA20,MA30));
MMIN:=MIN(MIN(MA5,MA10),MIN(MA20,MA30));
粘合:=(MMAX-MMIN)/C<=0.03;
趋势位:=C>MA60 AND (MA60>MA120 OR MA60>REF(MA60,5));
K1:=涨幅1>=0.05 AND 实体比1>0.6 AND 上影1<=实体1*0.5;
HK_CTJ:=K1 AND 倍量1 AND 穿线数>=3 AND 粘合 AND 趋势位;

{--- 大阳共振 ---}
实体2:=ABS(C-O);
振幅2:=H-L;
实体比2:=实体2/(振幅2+0.001);
上影2:=H-MAX(C,O);
量能2:=V>=MA(V,5)*1.5 AND V<=MA(V,5)*3;
多头2:=MA5>MA10 AND MA10>MA20 AND MA20>MA60 AND C>MA60;
突破2:=C>=HHV(H,20) OR (REF(C,1)<MA60 AND C>MA60);
K2:=涨幅1>=0.05 AND 实体比2>0.5 AND 上影2<=实体2/3;
MAC2:=DIF>0 AND DEA>0 AND DIF>DEA AND MACD柱>REF(MACD柱,1);
HK_DY:=K2 AND 量能2 AND 多头2 AND 突破2 AND MAC2;

{--- 中线 ---}
趋势ZX:=MA20>MA60 AND MA60>MA250 AND MA250>=REF(MA250,20);
价位ZX:=C>MA20 AND C<=MA20*1.02 AND C>O;
动量ZX:=RSI14>50 AND RSI14>REF(RSI14,1) AND DIF>DEA AND MACD柱>0;
HK_ZX:=趋势ZX AND 价位ZX AND 动量ZX;

{--- 优先级：文档建议大阳优先于朝天椒；选股 OR 后人工看主图即可 ---}
洪战法融合:HK风控 AND (HK_CTJ OR HK_DY OR HK_ZX);

洪战法融合;
```

若最后一行必须命名为选股输出，部分版本可改为仅：

```text
洪战法融合;
```

---

## 六、主图叠加（可选）

用于看盘标记信号日，**不用于条件选股**时把 `选股` 换成 `DRAWICON` 等：

```text
{示例：融合信号图标 — 日线}
{同上定义 HK_CTJ、HK_DY、HK_ZX、HK风控 后}
信号:=HK风控 AND (HK_CTJ OR HK_DY OR HK_ZX);
DRAWICON(信号,L*0.98,1);
```

图标编号按软件自带图标表调整。

---

## 七、参数速查（与 `strategy_hong_zhanfa.md` 一致）

| 参数 | 朝天椒 | 大阳 | 中线 |
|------|--------|------|------|
| 涨幅 | ≥5% | ≥5% | 回踩 MA20 收阳 |
| 实体/振幅 | >60% | >50% | — |
| 上影/实体 | ≤1/2 | ≤1/3 | — |
| 量 | 昨×2 且 5 均×1.5～5 | 5 均×1.5～3 | 未展开 |
| RPS | 120≥80 或 250≥80 | 同左 | 同左 |
| 低位 | 250 日低点未翻倍 | 同左 | 同左 |

---

## 八、报错与维护

1. **未知函数 EXTDATA_USER**：未装扩展数据或未执行「盘后数据下载 + 扩展数据刷新」。  
2. **公式过长**：拆成「公共 + 三个子条件」在选股器里 **组合选股**；或删 `换手限`、放宽 RPS。  
3. **结果过多**：提高 `RPS120` 阈值（如 85）、或要求 `大阳`/`朝天椒` 单独跑。  
4. **与 Python 结果不一致**：以本仓库 `tdx_stock_screener.py` / 后续 `hong_zhanfa` 模块为准做二次校验。

---

*文档版本：与 `docs/strategy_hong_zhanfa.md` 量化实现版同步。*
