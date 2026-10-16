# 评测集

- `evals.json`：行为评测。每条含用户请求、期望行为和可逐项判定的 expectations，覆盖默认江西全省、岗位映射与自定义岗位、关注池建立与CSV导入、拒绝内部客户名单、省外请求、关注池专题、拒绝营销推断等。
- `trigger-evals.json`：触发评测，`should_trigger` 标明该请求是否应启用本技能，用于调整 `SKILL.md` 的 description。

发版前逐条运行并记录通过率，通过率低于上一版本时不发版。每个确认的坏案例若属流程判断问题，补一条评测。详见 [docs/operations.md](../docs/operations.md#4-评测集)。
