from app.safety import closest_approach, assess


def test_converging_diverging_and_vertical_separation():
    assert closest_approach([0,0,100],[10,0,0],[200,0,100],[-10,0,0]) == {'seconds_to_closest':10.0,'separation_m':0.0}
    assert closest_approach([0,0,100],[-10,0,0],[200,0,100],[10,0,0])['seconds_to_closest'] == 0
    assert closest_approach([0,0,100],[10,0,0],[200,0,200],[-10,0,0])['separation_m'] == 100


def test_actual_speed_filtered_velocity_and_unconfirmed_track():
    v = dict(id='A',position=[0,0,100],armed=True,mission=[[1000,0,100]],wp=0,mode='AUTO',speed=5)
    track = dict(state='confirmed',estimate=[200,0,100],velocity=[-5,0,0],track=[])
    assert assess([v],track)['conflicts'][0]['seconds_to_closest'] == 20
    track['state']='tentative'
    assert assess([v],track)['conflicts'] == []
    track.update(state='confirmed',estimate=[200,0,200])
    assert assess([v],track)['conflicts'] == []
