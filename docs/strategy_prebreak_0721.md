# 0721参考线潜在突破策略

## 策略定位

这是一套“突破前一拍”的技术筛选，不预测涨停，也不把形态候选直接称为龙头。
真正的龙头还需要行业催化、板块联动、成交额排名和突破后的持续承接确认。

筛选分为三步：

1. 位置：收盘位于0721收盘价下方8%至上方3%，避免离突破线太远或已经加速。
2. 修复：收盘接近或站上MA20，MA5向上，MA20不再快速下降，近5日不过热。
3. 强度：RPS20至少位于全市场前35%，优先前20%；最近三日缩量，同时上涨日成交量
   不弱于下跌日成交量。

Python全市场策略还检查7月时序最大回撤、20日成交额、ATR、五日区间、除权异常和
ST/退市风险名称。输出的 `leader_score` 是技术准备度，不是上涨概率。

## 通达信形态预筛公式

通达信公式负责产生宽口径形态池；全市场RPS排名及精确时序回撤继续由本地脚本复核。

```text
D0701:=1260701;
D0721:=1260721;
D0731:=1260731;
D0806:=1260806;

JULYDAYS:=IF(DATE>=D0701,BARSLAST(DATE=D0701)+1,1);
JULYRUNHIGH:=HHV(H,JULYDAYS);
PRIORPEAK:=REF(JULYRUNHIGH,1);
DD:=IF(DATE>D0701 AND DATE<=D0731 AND PRIORPEAK>0,
       (PRIORPEAK-L)/PRIORPEAK,0);
B731:=BARSLAST(DATE=D0731);
MAXDD7:=REF(HHV(DD,23),B731);

C721:=REFDATE(C,D0721);
NEAR721:=C/C721>=0.92 AND C/C721<1.00;
TRENDREPAIR:=C>=MA(C,20)*0.99
             AND C/MA(C,20)<=1.06
             AND MA(C,5)>=MA(C,20)*0.96
             AND MA(C,5)>REF(MA(C,5),3)
             AND MA(C,20)>=REF(MA(C,20),5)*0.97;
NOTHOT:=C/REF(C,5)<=1.15 AND C/REF(C,1)<=1.07;
VOLCALM:=MA(V,3)<=REF(MA(V,20),3)*1.10;
LIQUID:=MA(AMOUNT,20)>=100000000;
DDOK:=MAXDD7>=0.25 AND MAXDD7<=0.55;
NOST:=NOT(NAMELIKE('ST'))
      AND NOT(NAMELIKE('*ST'))
      AND NOT(NAMELIKE('退'));

XG:DATE=D0806
   AND NEAR721 AND TRENDREPAIR AND NOTHOT
   AND VOLCALM AND LIQUID AND DDOK
   AND BARSCOUNT(C)>=125 AND NOST;
```

若坚持原始条件，把 `DDOK` 改成 `MAXDD7>=0.30 AND MAXDD7<=0.45`。预筛结果还必须
计算RPS20；没有相对强度确认的近线横盘，不属于龙头候选。

## 触发与淘汰

- 预触发：收盘突破0721收盘价和此前5日最高价中的较高者，且成交量达到20日均量
  1.2倍以上。
- 强确认：收盘进一步突破0721最高价，板块内至少还有两只同方向股票同步增强。
- 淘汰：冲线当日长上影、收盘回到0721收盘价下方，或放量跌破最近平台低点。
- 不追：突破时近5日涨幅已超过15%，或价格距离MA20超过10%。
