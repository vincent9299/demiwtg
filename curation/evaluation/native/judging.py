"""One blind judge request per answer, using one pre-answer packet per question."""
from pathlib import Path

from curation.preparation.materials import pixels
from curation.preparation.records import digest, read_record
from curation.evaluation.native.prompting import apply_native, fill, prepare_native, template
from curation.evaluation.native.rubrics import image_values, verify_rubrics
from curation.evaluation.native.scores import failure_score, normalize_score


class PrepareJudge:
    def __init__(self, run, pack, config):
        self.run, self.pack, self.config = Path(run), pack, config
        self.packets = {qid: read_record(entry['record_ref']) for qid, entry in verify_rubrics(run).items()}

    def __call__(self, row):
        try:
            packet = self.packets[row['task_id']]
            if row['question'] != packet['question']:
                raise ValueError('Answer question differs from frozen rubric')
            if row['status'] == 'model_failure' and not row.get('image'):
                return {**row, 'judge_status': 'scored_model_failure', 'score': failure_score(packet)}
            if row['status'] != 'generated':
                return {**row, 'judge_status': 'not_generated',
                        'judge_reason': 'No score for ' + row['status']}
            q = packet['question']
            pixels(row['image'])
            candidate = 'c_' + digest({'job': row['job_id'], 'image': row['image']['sha256'],
                                        'packet': digest(packet)})[:24]
            assets = []
            if q.get('edit_source'):
                assets.append((q['edit_source'], 'before', 'BEFORE'))
            assets.append((row['image'], 'after' if q['task_type'] == 'edit' else 'result',
                           'AFTER' if q['task_type'] == 'edit' else 'RESULT'))
            assets += [(i['asset'], 'evidence', i['label']) for i in packet['evidence_images']]
            images, roles = image_values(assets, self.run / 'blind' / candidate)
            system, user = template(q['task_type'], q.get('edit_type'))
            # Only these fields enter the model. Original task-review verdicts, origin paths,
            # model/arm IDs and actual answer retrieval never enter this payload.
            user = fill(user, {'DELIVERY_STATUS': 'image_available', 'INSTRUCTION': q['instruction'],
                               'RUBRIC_JSON': packet['rubric'], 'EVIDENCE_JSON': packet['evidence']})
            for marker, role in [('<BEFORE_IMAGE>', 'before'), ('<AFTER_IMAGE>', 'after'),
                                 ('<RESULT_IMAGE>', 'result')]:
                matching = [r for r in roles if r['role'] == role]
                if matching:
                    user = user.replace(marker, '见实际图像' + str(matching[0]['number']))
            user = user.replace('<EVIDENCE_IMAGES_IN_CATALOG_ORDER>',
                                '\n'.join(f"{r['label']}：实际图像{r['number']}" for r in roles if r['role'] == 'evidence')
                                or '本题没有核验配图。')
            values = {'instructions': system, 'payload': user, 'images': images}
            return prepare_native({**row, 'candidate_id': candidate, 'judge_packet_sha256': digest(packet)},
                                  'judge', candidate, values, roles, self.run, self.pack, self.config)
        except (KeyError, ValueError, OSError, TypeError) as error:
            return {**row, 'judge_status': 'input_error', 'judge_reason': str(error)}


class ApplyJudge:
    def __init__(self, run, config):
        self.run, self.config = Path(run), config
        self.packets = {qid: read_record(entry['record_ref']) for qid, entry in verify_rubrics(run).items()}

    def __call__(self, row):
        row, result = apply_native(row, 'judge', self.run, self.config)
        if result is None:
            return row
        try:
            packet = self.packets[row['task_id']]
            if row['judge_packet_sha256'] != digest(packet):
                raise ValueError('Judge packet changed after request')
            return {**row, 'judge_status': 'scored', 'score': normalize_score(result, packet)}
        except (ValueError, KeyError, TypeError, AttributeError, IndexError) as error:
            return {**row, 'judge_status': 'invalid_response', 'judge_reason': str(error)}


def verify_answer_assets(answers):
    # Run outside any reusable checkpoint to catch tampered result pixels.
    for answer in answers:
        if answer.get('image'):
            pixels(answer['image'])
