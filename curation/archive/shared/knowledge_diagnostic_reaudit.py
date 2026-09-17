"""Twelve source-bound visual observations made by root; not official scores."""

# Archive relocation: resolve the project, not a fixed directory depth.
from pathlib import Path as _ArchivePath
import sys as _archive_sys
_ARCHIVE_ROOT = next(p for p in _ArchivePath(__file__).resolve().parents if (p / "AGENTS.md").is_file() and (p / "curation").is_dir())
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT))
_archive_sys.path.insert(0, str(_ARCHIVE_ROOT / "curation/archive/compat/knowledge_application_v1"))
from knowledge_diagnostics import add_audit, render
B='expansion20_v1'


def annotation(status,evidence,*reasons):
    return dict(status=status,evidence=evidence,reasons=list(reasons or ['clear_visible_evidence']))


def save(q,model,cond,a,obs,issue=None):
    return add_audit(B,model,q+'__'+cond+'__r1',a,obs,issue)


def main():
    q='dev20_edit_hydrostatic'
    save(q,'bagel','baseline',{
      'K1':annotation('unverified','左红瓶仅略倾，红色延伸至不透明瓶领，未见可辨气液界面；底部椭圆是瓶底，不能当液面。','prerequisite_not_realized','output_visibility_failure'),
      'K2':annotation('unverified','未形成题面要求的明显倾斜与可见液体重排，不能仅据红色仍在瓶内判这一复合项满足。','prerequisite_not_realized')},[
      {'kind':'execution_observation','evidence':'用平木台托瓶，没有楔块支撑的约30度左倾。'},
      {'kind':'preservation_observation','evidence':'原图最右黄色液体瓶变为绿色，瓶子位置关系也改变。'}])
    save(q,'bagel','text',{
      'K1':annotation('unverified','红瓶仍直立，虽有可见水平界面，却没有实现倾斜条件，不能确认条件知识应用。','prerequisite_not_realized'),
      'K2':annotation('unverified','目标瓶未倾斜，无法核验因倾斜发生的液体重排；不能以密封未泄漏替代整个复合判据。','prerequisite_not_realized')},[
      {'kind':'execution_observation','evidence':'没有倾斜红瓶或添加楔块。'},
      {'kind':'preservation_observation','evidence':'原右侧黄色瓶被绿色瓶替代，源图布置改变。'}])
    for cond in ['baseline','text']:
        save(q,'gemini',cond,{
          'K1':annotation('met','红瓶明显斜置，气液交界呈与台面水平面相容的椭圆截面，没有随瓶长轴倾斜。'),
          'K2':annotation('met','红液位于斜瓶较低部分，瓶中留有上方空气区，未见液体泄漏；不是照搬直立瓶液体形状。')},[
          {'kind':'execution_observation','evidence':'瓶颈相对瓶底向画面右侧偏，与题面向左相反；液面物理规则与倾斜左右无关，所以方向错不连带否定K。'},
          {'kind':'preservation_observation','evidence':'其余透明、绿色、黄色三瓶仍可辨，输出为方图，场景边界裁切；是否构成旧主分保持失败须按原协议独立判，本文不打主分。'}])
    q='exp20_t2i_jiaotai'
    issue={'status':'review_pending','evidence':'两条K存在贯穿纹理的重叠，后续题应去重但本次不改冻结项；碗参考图只支持表面，不能支持断面。原题已明确要求断片内外及断面清楚可见，输出未展示不自动构成题目无效。','scope':'rubric_overlap_and_image_support; not a declaration of invalid question'}
    save(q,'bagel','baseline',{
      'K1':annotation('unverified','输出为完整浅青碗，没有新鲜断面可检验胎体。','output_visibility_failure'),
      'K2':annotation('unmet','清楚可见的内壁基本为单一浅青色，没有知识要求的双色泥纹；即使断面缺失，这一可见表面已构成反证。')},[
      {'kind':'execution_observation','evidence':'没有生成大断片，也未同时展示内外面与新鲜断面。'}],issue)
    save(q,'bagel','multimodal',{
      'K1':annotation('unverified','仍是连续碗状几何；上部斜边像碗口，无法确认新鲜破断面，不能从表面大理石样纹推断胎体。','output_visibility_failure'),
      'K2':annotation('unverified','虽有深浅色区和细纹，但内外面与真实破断面不能同时识别，不能核验三者相容的双色泥纹。','output_visibility_failure')},[
      {'kind':'execution_observation','evidence':'未清楚实现修复断片的展示要求；不以像石材的整体印象替代明确工艺错误判定。'}],issue)
    save(q,'gemini','baseline',{
      'K1':annotation('unverified','表面泥纹明显，但左前沿破断面很窄且多呈褐色，原生图仍不足以确定断面内部双色泥纹。','output_visibility_failure','reviewer_uncertainty'),
      'K2':annotation('unverified','内外表面相容，破断面纹理未能可靠核验；表面符合不等于整个K满足。','output_visibility_failure','reviewer_uncertainty')},[
      {'kind':'execution_observation','evidence':'额外加入了带文字的标签，违反无文字要求；断面展示未达到清楚可验的要求。'}],issue)
    save(q,'gemini','multimodal',{
      'K1':annotation('met','前侧宽破断面有深褐与浅色泥层交织，边缘厚度内可见，不是表面着色包着单色胎芯。'),
      'K2':annotation('met','内壁、左外壁与前侧破断面均为相容的深浅泥纹；不要求各面纹样逐像素延续。')},[
      {'kind':'execution_observation','evidence':'大断片与内外面／断面可辨，未见附加文字。此为观察，不替代旧T2I十轴完整评分。'}],issue)
    q='dev20_edit_scout'
    save(q,'bagel','baseline',{'K1':annotation('unmet','目标手为分开的伸指形状，未形成三指并拢及拇指压小指结构。')},[
      {'kind':'execution_observation','evidence':'树上手影仍有OK圆圈，与新举手形状不一致。'},
      {'kind':'preservation_observation','evidence':'原衣服文字位置出现彩色大徽章，超出手势编辑要求。'}])
    save(q,'bagel','multimodal',{'K1':annotation('unmet','可见手掌五指展开，小指未折回、拇指未压住小指，符合明确反证，不只是看不清。')},[
      {'kind':'preservation_observation','evidence':'原探险服成年男子及树影场景被童军制服青年、白色雕像及园林替换。'},
      {'kind':'reference_use_observation','evidence':'输出人物服饰及白色雕像背景与参考图片相近；行为上疑似混用图片角色，不能据此断言内部注意力机制。'}])
    for cond in ['baseline','multimodal']:
        save(q,'gemini',cond,{'K1':annotation('met','三根中央长指并拢直立，小指折回并被拇指覆盖，原OK圆圈消失。')},[
          {'kind':'execution_observation','evidence':'树上手影变为伸指轮廓，没有保留原OK圆圈。'},
          {'kind':'preservation_observation','evidence':'仍为原男子的帽子、探险服和同一树干场景；完整保持分另依旧协议核验，不由本观察生成。'}])
    render()


if __name__=='__main__':main()
