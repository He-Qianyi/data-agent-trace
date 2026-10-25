# 诊断证据组织

默认按manifest登记的benchmark、agent和分数状态组织证据，保留0分与缺失分数的区别，不根据脚本关键词推断失败原因。真正的语义诊断可用指定冻结方法入口，默认不运行旧Diagnostician。配置在 `configs/05_diagnosis`，输出在 `artifacts/05_diagnosis`。
