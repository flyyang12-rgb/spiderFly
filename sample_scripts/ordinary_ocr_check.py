"""Local PaddleOCR check using an image of ordinary, generated printed text.

Pass local PP-OCRv6 detection/recognition directories; this check does not
download models or accept external images. Outputs are UTF-8 JSON and PNG.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-root', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--font', type=Path, default=Path(r'C:\Windows\Fonts\msyh.ttc'))
    parser.add_argument('--mkldnn', choices=('default', 'on', 'off'), default='off')
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    output = args.output_dir or Path(tempfile.mkdtemp(prefix='ordinary-ocr-'))
    output.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    report = {'status': 'running', 'mkldnn': args.mkldnn, 'phases': []}

    def record(phase, **facts):
        item = {'phase': phase, 'elapsed_seconds': round(time.perf_counter() - started, 3), **facts}
        report['phases'].append(item)
        print(json.dumps(item, ensure_ascii=False), flush=True)

    try:
        from importlib.metadata import version
        names = ('PP-OCRv6_medium_det', 'PP-OCRv6_medium_rec')
        models = [args.model_root.resolve() / name for name in names]
        for path in models:
            for filename in ('inference.json', 'inference.pdiparams'):
                if not (path / filename).is_file():
                    raise FileNotFoundError(f'Local model file is missing: {path / filename}')
        if not args.font.is_file():
            raise FileNotFoundError('Pass an installed font supporting Chinese with --font')
        os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'
        record('packages', versions={name: version(name) for name in ('paddlepaddle', 'paddleocr', 'paddlex')})
        from PIL import Image, ImageDraw, ImageFont
        from paddleocr import PaddleOCR
        image = Image.new('RGB', (960, 200), 'white')
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype(str(args.font), 36)
        expected = ['SpiderFly OCR test 2026', '普通文字识别测试']
        for top, text in zip((24, 100), expected):
            draw.text((24, top), text, font=font, fill='black')
        image_path = output / 'ordinary-text.png'
        image.save(image_path)
        record('image_created')
        options = dict(device='cpu', cpu_threads=2,
                       use_doc_orientation_classify=False, use_doc_unwarping=False,
                       use_textline_orientation=False,
                       text_detection_model_name=names[0], text_recognition_model_name=names[1],
                       text_detection_model_dir=str(models[0]), text_recognition_model_dir=str(models[1]))
        if args.mkldnn != 'default':
            options['enable_mkldnn'] = args.mkldnn == 'on'
        engine = PaddleOCR(**options)
        record('model_loaded')
        normalize = lambda value: ''.join(value.split()).casefold()
        for number in (1, 2):
            inference_started = time.perf_counter()
            results = list(engine.predict(str(image_path)))
            texts = [str(text) for result in results for text in result.get('rec_texts', [])]
            record('recognized', pass_number=number, texts=texts,
                   inference_seconds=round(time.perf_counter() - inference_started, 3))
            actual = normalize(''.join(texts))
            if any(normalize(text) not in actual for text in expected):
                raise AssertionError('The recognized text did not match the generated image')
        report['status'] = 'success'
        record('complete')
        return 0
    except Exception as error:
        report['status'] = 'failed'
        record('error', error=type(error).__name__ + ': ' + str(error))
        return 1
    finally:
        (output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    raise SystemExit(main())
