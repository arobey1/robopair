import cv2
import os

# Set the path to the directory containing frames
base = 'ConstructionZone-backup'
frame_folder = f'{base}/Images'
output_video = f'{base}/video.mp4'

# Get the list of all frames
frames = [f for f in os.listdir(frame_folder) if f.endswith('.jpg')]
frames.sort()  # Ensure frames are in the correct order

# Read the first frame to get the video properties
frame = cv2.imread(os.path.join(frame_folder, frames[0]))
height, width, layers = frame.shape

# Define the codec and create a VideoWriter object
fourcc = cv2.VideoWriter_fourcc(*'mp4v')
video = cv2.VideoWriter(output_video, fourcc, 12, (width, height))

# Add each frame to the video
for frame_name in frames:
    frame = cv2.imread(os.path.join(frame_folder, frame_name))
    video.write(frame)

# Release the VideoWriter object
video.release()

print(f'Video saved as {output_video}')
