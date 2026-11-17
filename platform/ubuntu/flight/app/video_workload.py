"""Offline test footage and official COCO detector; no onboard-video claim.

Only administrator-configured local assets are read. Runtime inputs contain an
immutable JPEG, so retries always infer the same captured frame.
"""
import base64
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import time

MODEL = 'ssdlite320_mobilenet_v3_large_coco-a79551df.pth'
MODEL_SHA256 = 'a79551df90c79834bcd3bb3845ef9d966b5449a3a9b2833ae8404778ca5d65d2'
VIDEO_SHA256 = '45cddc9490be69345cbdab64ca583be65987e864ca408038e648db99e10516cf'
ASSETS = Path(os.environ.get('FLIGHT_VISION_ASSETS', '/tmp/lowair-vision-assets'))
_model = None
_categories = None


def available():
    return all(importlib.util.find_spec(name) is not None for name in ('cv2', 'torch', 'torchvision')) and (ASSETS / MODEL).is_file() and (ASSETS / 'vtest.avi').is_file()


def evidence_path(ident):
    if not re.fullmatch(r'[a-f0-9]{64}', ident):
        raise ValueError('Invalid frame hash')
    root = Path(os.environ.get('FLIGHT_RECORDS_DIR', os.environ.get('FLIGHT_DATA_DIR', Path(__file__).resolve().parents[1] / 'data')))
    return root / 'video-frames' / (ident + '.jpg')


class Source:
    def __init__(self):
        import cv2
        if not available():
            raise ValueError('测试视频/模型未就绪，请运行 scripts/setup_video_workload.py')
        if hashlib.sha256((ASSETS / MODEL).read_bytes()).hexdigest() != MODEL_SHA256:
            raise ValueError('模型校验失败，请重新下载官方权重')
        if hashlib.sha256((ASSETS / 'vtest.avi').read_bytes()).hexdigest() != VIDEO_SHA256:
            raise ValueError('测试录像校验失败，请重新下载')
        self.cap = cv2.VideoCapture(str(ASSETS / 'vtest.avi'))
        self.fps = self.cap.get(cv2.CAP_PROP_FPS)
        self.count = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if not self.cap.isOpened() or self.fps <= 0 or self.count <= 0:
            self.cap.release()
            raise ValueError('测试视频无法解码')
        self.started = time.time()
        self.source_sha256 = hashlib.sha256((ASSETS / 'vtest.avi').read_bytes()).hexdigest()

    def capture(self):
        import cv2
        captured_at = time.time()
        index = int((captured_at - self.started) * self.fps) % self.count
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = self.cap.read()
        if not ok:
            raise RuntimeError('测试视频帧读取失败')
        ok, encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            raise RuntimeError('测试视频帧编码失败')
        data = encoded.tobytes()
        ident = hashlib.sha256(data).hexdigest()
        path = evidence_path(ident)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            temporary = path.with_suffix('.tmp')
            temporary.write_bytes(data)
            temporary.replace(path)
        return dict(jpeg=base64.b64encode(data).decode(), frame=dict(
            sha256=ident, url='/api/test-video/frames/' + ident,
            captured_at=captured_at, video_time_s=index / self.fps, frame_index=index,
            source='opencv-vtest-4.11.0', source_sha256=self.source_sha256,
            width=frame.shape[1], height=frame.shape[0], onboard=False))

    def close(self):
        self.cap.release()


def decode(value):
    import cv2
    import numpy as np
    raw = base64.b64decode(value['jpeg'], validate=True)
    if hashlib.sha256(raw).hexdigest() != value['frame']['sha256']:
        raise ValueError('Frame checksum mismatch')
    image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError('Invalid JPEG')
    return image


def detect(value):
    global _model, _categories
    boot_started = time.perf_counter()
    import cv2
    import torch
    from torchvision.models.detection import ssdlite320_mobilenet_v3_large, SSDLite320_MobileNet_V3_Large_Weights
    load_s = 0
    if _model is None:
        started = boot_started
        torch.set_num_threads(1)
        _model = ssdlite320_mobilenet_v3_large(weights=None, weights_backbone=None)
        _model.load_state_dict(torch.load(ASSETS / MODEL, map_location='cpu', weights_only=True))
        _model.eval()
        _categories = SSDLite320_MobileNet_V3_Large_Weights.COCO_V1.meta['categories']
        load_s = time.perf_counter() - started
    image = decode(value)
    tensor = torch.from_numpy(cv2.cvtColor(image, cv2.COLOR_BGR2RGB)).permute(2, 0, 1).float() / 255
    # Overlapping 60% crops preserve small pedestrians in the 320px detector.
    # Merge in original-frame coordinates with class-aware NMS, not fake boxes.
    from torchvision.ops import batched_nms
    height, width = image.shape[:2]
    cw, ch = int(width * .6), int(height * .6)
    origins = [(0, 0), (width-cw, 0), (0, height-ch), (width-cw, height-ch)]
    tick = time.perf_counter()
    with torch.inference_mode():
        predictions = _model([tensor[:, y:y+ch, x:x+cw] for x, y in origins])
    all_boxes, scores, labels = [], [], []
    for (x, y), prediction in zip(origins, predictions):
        selected = prediction['scores'] >= .45
        all_boxes.append(prediction['boxes'][selected] + torch.tensor([x, y, x, y]))
        scores.append(prediction['scores'][selected])
        labels.append(prediction['labels'][selected])
    all_boxes, scores, labels = torch.cat(all_boxes), torch.cat(scores), torch.cat(labels)
    kept = batched_nms(all_boxes, scores, labels, .45)[:30]
    inference_s = time.perf_counter() - tick
    boxes = [dict(box=[round(float(v), 2) for v in all_boxes[i]], score=round(float(scores[i]), 5),
                  class_id=int(labels[i]), label=_categories[int(labels[i])]) for i in kept]
    return dict(frame=value['frame'], detections=boxes, model='SSDLite320 MobileNetV3 COCO_V1',
                threshold=.45, tiling='2x2-overlap-60pct-nms045', inference_s=inference_s, model_load_s=load_s,
                weights_sha256=hashlib.sha256((ASSETS / MODEL).read_bytes()).hexdigest())


def execute(task, inputs, position):
    if task == 'preprocess':
        value = inputs['video']
        decode(value)  # Validate actual immutable frame before downstream execution.
        return value
    if task == 'detect':
        return detect(inputs['preprocess'])
    if task == 'segment':
        # Existing DAG name retained: this stage is image statistics, not semantic segmentation.
        import cv2
        image = decode(inputs['preprocess'])
        return dict(mean_luminance=float(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).mean()))
    if task == 'fusion':
        return dict(**inputs['detect'], **inputs['segment'], sitl_position=position)
    if task == 'alert':
        value = inputs['fusion']
        return dict(**value, target_count=len(value['detections']), synthetic=False,
                    label='测试录像目标检测；不代表园区异常或机载观测', onboard=False)
    raise ValueError('Unsupported fixed workload')
