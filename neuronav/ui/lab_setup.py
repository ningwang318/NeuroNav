"""Appearance-only lab scene inferred from a single reference photograph.

Robot posture, base placement, tripod and breadboard are approximate static
illustrations. They must never be used for robot motion or collision checking.
"""
import numpy as np
from neuronav.core.frames import rotation
from neuronav.ui.camera_bar import cylinder, case
from neuronav.core.table_layout import TableLayout

WHITE = (.87,.89,.91,1)
SILVER = (.50,.54,.58,1)
BLACK = (.15,.17,.19,1)
GREEN = (.45,.72,.12,1)
GROUND_Y = -.09  # breadboard underside and tripod feet share this level


def axial(direction):
    z = np.asarray(direction,dtype=float)
    z /= np.linalg.norm(z)
    seed = np.array([0.,1.,0.]) if abs(z[1]) < .9 else np.array([1.,0.,0.])
    x = np.cross(seed,z)
    x /= np.linalg.norm(x)
    return np.column_stack([x,np.cross(z,x),z])


def sphere(radius, rows=14, columns=24):
    vertices = []
    for i in range(rows+1):
        theta = np.pi*i/rows
        for j in range(columns):
            phi = 2*np.pi*j/columns
            vertices.append([radius*np.sin(theta)*np.cos(phi),
                             radius*np.cos(theta), radius*np.sin(theta)*np.sin(phi)])
    faces = []
    for i in range(rows):
        for j in range(columns):
            k = (j+1)%columns
            a,b,c,d = i*columns+j,i*columns+k,(i+1)*columns+j,(i+1)*columns+k
            if i:
                faces.append([a,c,b])
            if i < rows-1:
                faces.append([b,c,d])
    return np.asarray(vertices),np.asarray(faces)


def curved_link(start,end,radius,bow):
    """Smooth swept shell with rounded profile and a gently bowed centreline."""
    start,end = np.asarray(start),np.asarray(end)
    rings, sides = 18, 28
    t = np.linspace(0,1,rings)
    path = start+(end-start)*t[:,None]+np.sin(np.pi*t)[:,None]*np.asarray(bow)
    vertices=[]
    for i in range(rings):
        tangent = path[min(i+1,rings-1)]-path[max(i-1,0)]
        basis = axial(tangent)
        r = radius*(.82+.18*np.sin(np.pi*t[i]))
        for angle in np.linspace(0,2*np.pi,sides,endpoint=False):
            vertices.append(path[i]+r*(np.cos(angle)*basis[:,0]+.88*np.sin(angle)*basis[:,1]))
    vertices.extend([start,end])
    faces=[]
    for i in range(rings-1):
        for j in range(sides):
            k=(j+1)%sides
            a,b,c,d=i*sides+j,i*sides+k,(i+1)*sides+j,(i+1)*sides+k
            faces.extend([[a,b,c],[b,d,c]])
    for j in range(sides):
        k=(j+1)%sides
        faces.extend([[rings*sides,j,k],[rings*sides+1,(rings-1)*sides+k,(rings-1)*sides+j]])
    return np.asarray(vertices),np.asarray(faces)


def lab_setup_meshes(cameras, reference, table=None):
    if reference is None:
        return [], [], []
    table=table or TableLayout()
    origin,layout=table.frame(reference)
    right,normal,back=layout[:,0],layout[:,1],layout[:,2]
    camera_center=np.mean([c[1] for c in cameras],axis=0) if cameras else origin+reference.basis[:,0]
    batches={}
    def add(geometry, point, orientation=np.eye(3), color=WHITE, smooth=False):
        vertices,faces=geometry
        vertices=vertices @ orientation.T+np.asarray(point)
        key=color,smooth
        batch=batches.setdefault(key,[[],[],0])
        batch[0].append(vertices)
        batch[1].append(faces+batch[2])
        batch[2]+=len(vertices)
    def position(local):
        return origin+layout @ np.asarray(local)
    def box(size, local, color, bevel=.004):
        # Primitive case local XY front; map its Y to scene depth, Z to up.
        orientation=np.column_stack([right,-back,normal])
        add(case(size[0],size[2],size[1],bevel),position(local),orientation,color)
    def rod(a,b,r,color,segments=24):
        a,b=np.asarray(a),np.asarray(b)
        add(cylinder(r,float(np.linalg.norm(b-a)),segments),(a+b)/2,axial(b-a),color,True)
    def local_rod(a,b,r,color):
        rod(position(a),position(b),r,color)

    # Photo estimate: 450 x 600 mm, 25 mm hole pitch, 50 mm edge margins.
    # The top is below the calibration-sphere centres, not at their plane.
    width,depth,thickness=np.array([table.width_mm,table.depth_mm,table.thickness_mm])/1000
    box((width,thickness-.016,depth),[0,-thickness/2,0],BLACK)
    box((width,.008,depth),[0,-.004,0],(.63,.65,.67,1))
    box((width,.008,depth),[0,-thickness+.004,0],(.40,.44,.48,1))
    for x in np.arange(-width/2+.05,width/2-.05+.0001,.025):
        for z in np.arange(-depth/2+.05,depth/2-.05+.0001,.025):
            add(cylinder(.0016,.00035,8),position([x,.0002,z]),axial(normal),(.12,.14,.16,1))
    # The breadboard is a low slab directly on the ground, with no table legs.

    base=np.array([table.robot_x_mm/1000,0,table.robot_z_mm/1000])
    box((.16,.015,.15),base+[0,.0075,0],SILVER)
    # Bolt heads on the square robot mounting plate.
    for x in [-.060,.060]:
        for z in [-.055,.055]:
            add(cylinder(.006,.004,12),position(base+[x,.014,z]),axial(normal),(.26,.29,.32,1))
    local_rod(base+[0,.012,0],base+[0,.18,0],.065,SILVER)
    local_rod(base+[0,.02,0],base+[0,.035,0],.076,(.64,.67,.70,1))
    local_rod(base+[0,.17,0],base+[0,.215,0],.069,WHITE)
    # Approximate folded xArm-style six-axis posture from the photo. There is
    # deliberately no kinematics or connection to robot joint telemetry.
    joints=np.array([[0,.19,0],[-.04,.25,0],[-.19,.56,0],
                     [.065,.66,0],[.105,.40,.025],[-.055,.29,.09]])+base
    radii=[.055,.057,.060,.056,.046,.041]
    for i in range(1,len(joints)):
        start,end=position(joints[i-1]),position(joints[i])
        bow=layout @ np.array([-.018 if i%2 else .018, .012, .012])
        add(curved_link(start,end,.048 if i<4 else .040,bow),np.zeros(3),color=WHITE,smooth=True)
    for i,(joint,radius) in enumerate(zip(joints,radii)):
        center=position(joint)
        axis=back if i in [1,2,3] else right if i==4 else normal
        add(sphere(radius),center,color=WHITE,smooth=True)
        rod(center-axis*.027,center+axis*.027,radius*.99,SILVER,40)
        for sign in [-1,1]:
            add(cylinder(radius*.95,.012,40),center+axis*sign*.033,
                axial(axis),WHITE,True)
            add(cylinder(.006,.0012,16),center+axis*sign*.0395,
                axial(axis),(.44,.48,.52,1),True)
    wrist=position(joints[-1])
    rod(wrist-normal*.035,wrist-normal*.065,.033,SILVER,40)
    rod(wrist-normal*.065,wrist-normal*.072,.038,(.30,.34,.38,1),40)

    if cameras:
        # Tripod is vertical relative to the marker plane. Its head joins the
        # calibrated camera bar; leg spread and floor height are photo estimates.
        forward=-np.mean([rotation(c[2])[:,2] for c in cameras],axis=0)
        forward/=np.linalg.norm(forward)
        mount=camera_center-forward*.025-normal*.05
        floor_y=-thickness
        height=np.dot(mount-origin,normal)-floor_y
        floor_center=mount-normal*height
        # Raise the leg junction so only a short centre neck remains below
        # the head. Keep the calibrated camera bar fixed in tracker space.
        hip=floor_center+normal*(height*.80)
        rod(hip,mount-normal*.10,.022,BLACK,40)
        rod(hip,hip+normal*.06,.034,(.16,.18,.20,1),32)
        rod(mount-normal*.10,mount-normal*.025,.032,BLACK,32)
        rod(mount-normal*.025,mount,.023,SILVER,32)
        add(sphere(.037),mount-normal*.06,color=(.12,.14,.16,1),smooth=True)
        # Tilt-head handles and green accent collars.
        rod(mount-normal*.065,mount-normal*.065+right*.13,.006,BLACK)
        rod(mount-normal*.065+right*.10,mount-normal*.065+right*.15,.011,BLACK)
        for angle in [0,2*np.pi/3,4*np.pi/3]:
            direction=right*np.cos(angle)+back*np.sin(angle)
            top=hip+direction*.028
            foot=floor_center+direction*.47+normal*.020
            vector=foot-top
            rod(top,top+vector*.52,.021,BLACK,32)
            rod(top+vector*.50,top+vector*.83,.017,(.12,.14,.16,1),32)
            rod(top+vector*.81,foot,.014,BLACK,28)
            for t in [.50,.81]:
                point=top+vector*t
                length=vector/np.linalg.norm(vector)*.014
                rod(point-length/2,point+length/2,.024,BLACK)
                rod(point-length/2,point-length/2+length*.13,.0245,GREEN)
            rod(foot-normal*.020,foot+normal*.020,.025,BLACK)
            # Hip hinge and support brace.
            hinge_axis=np.cross(normal,direction)
            rod(top-hinge_axis*.026,top+hinge_axis*.026,.031,BLACK)
            rod(top+hinge_axis*.026,top+hinge_axis*.028,.027,GREEN)
            rod(hip-normal*.27,top+vector*.37,.006,BLACK)
        # A thin hanging cable follows the centre column.
        cable_start=camera_center-forward*.055
        for t in np.linspace(0,1,24)[:-1]:
            def cable(u):
                return cable_start*(1-u)+(hip-normal*.12)*u+right*.045*np.sin(np.pi*u)
            rod(cable(t),cable(t+1/23),.002,(.045,.055,.065,1),8)

    # Neutral ground under the estimated tripod and table feet. Exclude this
    # backdrop from framing so it does not shrink the equipment unnecessarily.
    content_vertices=np.vstack([np.vstack(batch[0]) for batch in batches.values()])
    box((2.6,.016,2.2),[.25,-thickness-.008,.10],(.15,.18,.22,1),bevel=.018)
    meshes=[dict(vertices=np.vstack(batch[0]),faces=np.vstack(batch[1]),color=color,smooth=smooth)
            for (color,smooth),batch in batches.items()]
    bounds=[content_vertices.min(axis=0),content_vertices.max(axis=0)]
    labels=[(position(base+[0,.79,0]),'xArm 6 · approximate pose')]
    return meshes,labels,bounds
