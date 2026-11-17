"""FFmpeg looped OpenCV sample footage and immutable detection evidence."""
import asyncio
import shutil
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
from .video_workload import ASSETS, available, evidence_path

router = APIRouter(prefix='/api/test-video')

@router.get('/status')
def status():
    return {'available': bool(shutil.which('ffmpeg')) and available(), 'source': 'opencv-vtest-4.11.0',
            'transport': 'mjpeg', 'url': '/api/test-video/stream',
            'note': 'OpenCV 测试录像循环流；检测证据按计算批次保存，非机载摄像头'}

@router.get('/stream')
async def stream():
    executable = shutil.which('ffmpeg')
    if not executable or not available():
        raise HTTPException(503, 'FFmpeg 或测试录像/模型未就绪')
    process = await asyncio.create_subprocess_exec(
        executable, '-hide_banner', '-loglevel', 'error', '-re', '-stream_loop', '-1',
        '-i', str(ASSETS / 'vtest.avi'), '-an', '-c:v', 'mjpeg',
        '-q:v', '6', '-f', 'mpjpeg', 'pipe:1',
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    async def frames():
        try:
            while chunk := await process.stdout.read(65536):
                yield chunk
        finally:
            if process.returncode is None:
                process.terminate()
            await process.wait()
    return StreamingResponse(frames(), media_type='multipart/x-mixed-replace; boundary=ffmpeg',
                             headers={'Cache-Control': 'no-store', 'X-Video-Source': 'opencv-vtest-4.11.0'})


@router.get('/frames/{ident}')
def frame(ident: str):
    try:
        path = evidence_path(ident)
    except ValueError as exc:
        raise HTTPException(404, '帧不存在') from exc
    if not path.is_file():
        raise HTTPException(404, '帧证据不存在')
    return FileResponse(path, media_type='image/jpeg', headers={'Cache-Control': 'public, max-age=31536000, immutable'})
