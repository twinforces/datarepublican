import math

import math

def generate_spiral_svg_path(turns, max_radius=10, center_x=12, center_y=12, dtheta=0.1):
    """
    Generate an SVG path tag for a spiral.
    
    Parameters:
    - turns: Number of spiral turns (e.g., 3 for several wraps, 1.5 for half that).
    - max_radius: Maximum radius of the spiral (default 10 to fit a 20x20 area).
    - center_x, center_y: Center point of the spiral (default 12, 12 for a 24x24 SVG).
    - dtheta: Angular step size in radians (default 0.1 for smoothness).
    
    Returns:
    - A string containing the SVG <path> tag with the spiral path data.
    """
    theta_max = turns * 2 * math.pi  # Total angle based on number of turns
    n = int(theta_max / dtheta) + 1  # Number of points to generate
    path_data = ""  # Initialize the path string
    
    for i in range(n):
        theta = i * dtheta  # Current angle
        r = (theta / theta_max) * max_radius  # Radius increases linearly with angle
        x = center_x + r * math.cos(theta)  # X-coordinate
        y = center_y + r * math.sin(theta)  # Y-coordinate
        x_rounded = round(x, 2)  # Round to 2 decimal places
        y_rounded = round(y, 2)  # Round to 2 decimal places
        
        if i == 0:
            # Start the path with "M" (move to) for the first point
            path_data = f"M{x_rounded},{y_rounded}"
        else:
            # Add subsequent points with "L" (line to)
            path_data += f" L{x_rounded},{y_rounded}"
    
    # Return the path data wrapped in the <path> tag
    return f'<path d="{path_data}" stroke="black" stroke-width="2" fill="none" />'   
    
print("path 5", generate_spiral_svg_path(5, 10, 12, 12))
print("path 1.5", generate_spiral_svg_path(1.5, 10, 12, 12))
print("path 3", generate_spiral_svg_path(3, 10, 12, 12))
