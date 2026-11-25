# 用同一份盲标比较生词预测

先按《生词预测评估》准备一段视频的候选文件、已逐句确认的人工标注，以及标注前保存的用户画像。再用词难度表模块的生成命令取得 word-difficulty-v1.json。整个实验在本机完成。

```powershell
node tools/compare_word_models.cjs video.json labels.json profile.json word-difficulty-v1.json normal
```

命令输出三套结果。baseline 复现当前播放器；b_frequency 使用词频难度；b_initial 加入考纲阶段先验。三套使用同一份候选、重复次数上限、句内上限和显示优先级。用户亲自标记的生熟词直接沿用标记。对表内其余单词，IRT 可重新判断现行规则从考试词表、频率或旧测验推断出的生熟状态。短语、特殊义项和表外词沿用现行规则。旧快照如果缺少 marks，则把全部有效词库保留为例外，结果中的 overrideSource 会标明口径。coverage 分别统计这些情况，便于发现实验实际改动了多少次判词。

## 能力与阈值

两套实验按 sigmoid(a × (词难度 − θ − 领域偏移 + 观看折扣)) 估算不认识概率。more、normal、less 三档分别在 0.3、0.5、0.7 及以上触发注释。默认区分度为 2，两个偏移均为 0。这些参数有待用实测资料校准。

旧画像只有词汇量时，程序在每张表上分别反求能力，使所有收录词的期望认识数之和等于画像词汇量。该步骤与字幕答案无关。如有同尺度的独立测量，可把含 theta 数值的 JSON 文件作为最后一个参数。可选数值字段还有 discrimination、domainBias、viewingDiscount。跨难度表使用同一 theta 时，应先确认两套量尺可比。

单词能力试测页可直接导出 word-cat-parameters.json。将它放在命令末尾，评分器会核对原始难度表文件的 SHA-256 指纹，并分别应用 b_frequency 和 b_initial 对应的能力估计。请保留生成试测页时所用的同一份表。

## 怎样读结果

- display 或 baseline 以最终实际显示的注释计数，含命中、多注、漏注、召回和每分钟多注，也包括候选提取阶段已经漏掉的词。
- probability 只评估表中存在且未被个人标记覆盖的单词出现次数。若一个单词只与更长的人工标注部分重叠，例如学习者只标了整个短语，则该词自身是否认识不明。它仍参与实际显示评分，单词概率评分排除此类观察，overlappingLabels 单列其数量。logLoss 越小越好。AUC 衡量排序，在样本只有一个类别时返回 null。各校准区间同时给出样本数、平均预测概率和实际生词比例。
- 用标注前取得的画像，固定参数后再看留出的学习者或视频。若同一段素材用于调参，请单独保留评估数据。用人工标注人数、片段时长和覆盖率说明结果的适用范围。

## 多位试用者、多个片段

将每位学习者观看每段视频的文件写进一个本地 JSON 清单。路径相对于清单文件。learner 使用匿名代号，domain 用于分别查看科技、日常口语或新闻等片段。parameters 可省略，省略时使用该场的观看前词汇量反推能力。

```json
{
  "version": 1,
  "sessions": [
    {"learner":"P01","domain":"科技","video":"clips/tech.json","labels":"P01/tech-labels.json","profile":"P01/tech-before.json","parameters":"P01/word-cat-parameters.json"},
    {"learner":"P02","domain":"科技","video":"clips/tech.json","labels":"P02/tech-labels.json","profile":"P02/tech-before.json"}
  ]
}
```

```powershell
node tools/compare_word_cohort.cjs manifest.json word-difficulty-v1.json normal
```

overall 按全部观看场次累计命中、漏注、多注和总时长，再计算比例。byLearner 和 byDomain 可检查不同水平、不同内容的差异。AUC 和校准分箱从相应组内的全部单词出现次数重新计算。overrideSources 给出各组使用新旧快照的场次数。同一人可以评估同一长视频的多个独立片段。重复或交叠的区间会报错，重测可另存清单独立分析。
