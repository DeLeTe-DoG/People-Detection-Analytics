Модели распознавания лиц из проекта OpenCV Zoo (https://github.com/opencv/opencv_zoo):
  face_detection_yunet_2023mar.onnx          — YuNet, поиск лиц (лицензия MIT)
  face_recognition_sface_2021dec_int8.onnx   — SFace (квантованная, ~10 МБ), вектор лица (лицензия Apache-2.0)
Файлы входят в поставку. Если они пропали: python download_models.py
Или скачайте вручную и положите сюда (имена не менять):
  https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx
  https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_recognition_sface/face_recognition_sface_2021dec_int8.onnx
Проверка: python download_models.py --check

Важно: лица, снятые одной моделью, нельзя сравнивать с лицами другой модели. Программа запоминает,
какой моделью снято лицо, и при смене модели просит переснять сотрудников.
