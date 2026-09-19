from ultralytics import YOLO

model = YOLO("yolo26n.pt")

results = model(
    "videos/01_034-3.mp4",
    save=True
)

print("Detection completed.")