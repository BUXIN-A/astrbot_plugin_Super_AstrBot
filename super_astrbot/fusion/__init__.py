"""融合域：把各进程内子模块的状态聚合成「可编排、可诊断」的视图。

本包不做业务，只做**聚合与自检**：

- ``FusionStatusService.backends()``：记忆后端状态（本地 / LATRACE / 三级 / 衰减）；
- ``FusionStatusService.pipeline()``：群聊消息编排流水线 ①→⑧ 的逐段状态；
- ``FusionStatusService.health()``：各融合模块健康（含 ``degraded`` 占位）；
- 全部数据来自进程内模块的自报，不探测任何外部服务——
  「LATRACE 就绪」的含义是「进程内时序图谱可用」，不是「docker 连通」。
"""

from .status import FusionStatusService

__all__ = ["FusionStatusService"]
