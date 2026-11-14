from ultralytics import YOLO

model = YOLO("yolo26n.pt")

results = model(
    "videos/01_012-3.mp4",
    save=True
)

print("Detection completed.")