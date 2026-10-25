1 通达信公式EXTRS:(C-REF(C,N))/REF(C,N);
2 通达信扩展数据属性配置：精确复权，生成横向排名数据，排名方法0-1000归一化顺序，本地数据
3 刷新个股的RPS强度
4 板块RPS强度
5 选股策略如下
{
    HC120:=EXTDATA_USER(6,0);
{120天的RPS}
RPSHC120:=HC120/10;
HC250:=EXTDATA_USER(7,0);
{250天的RPS}
RPSHC250:=HC250/10;


SXHCG1:=(RPSHC120+RPSHC250>180);
{RPS120和RPS250之和大于185。这个185是可调参数，如180至185}

SXHCG20:=C>MA(C,20);
{收盘站上20日线}
SXHCG21:=COUNT(C>MA(C,250),30)>=20;
{过去30天的收盘价高于250日线的天数至少有25天。这个25天是可调参数，如25至30}
SXHCG22:=COUNT(C>MA(C,200),30)>=20;
{过去30天的收盘价高于200日线的天数至少有25天。这个25天是可调参数，如25至30}
SXHCG23:=COUNT(C>MA(C,20),10)>=7;
{过去10天的收盘价高于20日线的天数至少有9天}
SXHCG24:=COUNT(C>MA(C,10),4)>=3 AND COUNT(C>MA(C,20),4)>=3;
{过去4天的收盘价高于10日线和20日线的天数都至少有3天}
SXHCG2:=SXHCG20 AND SXHCG21 AND SXHCG22 AND (SXHCG23 OR SXHCG24);

新高天数:=HHVBARS(H,20);
新低天数:=IF(新高天数=0,0,LLVBARS(L,新高天数));
新高价:=REF(H,新高天数);
新低价:=REF(L,新低天数);
回撤幅度:=(新高价-新低价)/新高价;
SXHCG31:=回撤幅度<=0.25 AND COUNT(回撤幅度>0.25,新高天数)=0;
{过去20天内的最大回撤不超过25%}
SXHCG32:=C/HHV(C,120)>0.8;
{收盘价是一年最高价的0.8以上，这个0.8是可调参数，如0.75至0.85}
SXHCG3:=SXHCG31 AND SXHCG32;

SXHCG411:=EVERY(MA(C,20)>=REF(MA(C,20),1),5);
{过去5天的20日线一直保持上升状态。这个5天是可调参数，如5至15}
SXHCG412:=EVERY(MA(C,10)>=MA(C,20),5);
{过去5天的10日线一直大于20日线。这个5天是可调参数，如5至15}
SXHCG41:=SXHCG411 AND SXHCG412;
SXHCG421:=MA(C,10)>=REF(MA(C,10),1);
{10日线向上}
SXHCG422:=MA(C,20)>=REF(MA(C,20),1);
{20日线向上}
SXHCG423:=MA(C,10)>=MA(C,20);
{10日线在20日线上方}
SXHCG42:=SXHCG421 AND SXHCG422 AND SXHCG423;
SXHCG4:=SXHCG41 OR SXHCG42;

SXHCG5:=VOL/CAPITAL*100<10;
{日线换手率小于10%}
SXHCG6:=COUNT(C>MA(C,5),10) >= 6; {新增条件：过去10天内至少有1天收盘价在5日均线上方}

SXHCG7 := COUNT(AMOUNT > 100000000, 10) >= 7; {新增条件：过去10天内至少有7天成交额超过1亿}

VA_SHORT_PERIOD := 5; {堆量判断-短期均线周期，可调}
VA_LONG_PERIOD := 60; {堆量判断-长期均线周期，可调}
VA_RATIO_ := 1.8; {堆量判断-短期均量/长期均量阈值，可调，例如1.5, 1.8, 2.0}
VA_SHORT_MA := MA(VOL, VA_SHORT_PERIOD);
VA_LONG_MA := MA(VOL, VA_LONG_PERIOD);
SXHCG_VOLACC := VA_SHORT_MA > VA_LONG_MA * VA_RATIO_; {短期均量显著大于长期均量}
SXHCG8:=COUNT(C<MA(C,5),3) <= 1;
SXHCG:=SXHCG1 AND SXHCG2 AND SXHCG3 AND SXHCG4 AND SXHCG5 AND SXHCG7 AND SXHCG6 AND SXHCG8;
SXHCG;

}
