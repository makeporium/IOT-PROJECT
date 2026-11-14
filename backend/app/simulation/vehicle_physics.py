"""Vehicle geometry, door swing kinematics, and bounding dimensions."""

import math
from typing import List, Tuple


class VehiclePhysics:
    """Parked vehicle geometric properties and driver-side door opening envelope."""

    def __init__(
        self,
        car_length_m: float = 4.60,
        car_width_m: float = 1.82,
        door_length_m: float = 1.10,
        max_door_angle_deg: float = 65.0,
        lateral_safety_buffer_m: float = 0.50
    ):
        self.length = car_length_m
        self.width = car_width_m
        self.door_length = door_length_m
        self.max_door_angle = max_door_angle_deg
        self.safety_buffer = lateral_safety_buffer_m
        
        # Door hinge location relative to car center (x=left side, y=longitudinal offset)
        self.hinge_x = -car_width_m / 2.0
        self.hinge_y = 0.60  # hinge behind front bumper

    def get_door_zone_polygon(self, opening_angle_deg: float = 65.0) -> List[Tuple[float, float]]:
        """
        Calculates the 2D polygon footprint of the door opening danger corridor on the road.
        Points returned in meters relative to the car origin.
        """
        angle_rad = math.radians(opening_angle_deg)
        # Hinge point
        hx, hy = self.hinge_x, self.hinge_y
        
        # Tip of fully swung door
        tip_x = hx - self.door_length * math.sin(angle_rad)
        tip_y = hy - self.door_length * (1 - math.cos(angle_rad))
        
        # Danger buffer zone extending backwards along traffic direction
        corridor_width = abs(tip_x - hx) + self.safety_buffer
        
        polygon = [
            (hx, hy + 0.3),                                     # front of hinge
            (hx - corridor_width, hy + 0.3),                    # outer safety edge
            (hx - corridor_width, hy - self.door_length - 1.5), # rear approach buffer
            (hx, hy - self.door_length - 1.5)                   # car body flank
        ]
        return polygon
