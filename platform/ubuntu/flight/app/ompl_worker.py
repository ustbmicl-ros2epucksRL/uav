"""Official OMPL BIT* binding, JSON stdin/stdout, isolated random seed."""
import json
import sys
from ompl import base as ob, geometric as og, util as ou


def collision(a, b, box):
    enter, leave = 0.0, 1.0
    for i in range(3):
        d = b[i] - a[i]
        if abs(d) < 1e-12:
            if not box['lower'][i] <= a[i] <= box['upper'][i]:
                return False
        else:
            lo = (box['lower'][i] - a[i]) / d
            hi = (box['upper'][i] - a[i]) / d
            enter, leave = max(enter, min(lo, hi)), min(leave, max(lo, hi))
            if enter > leave:
                return False
    return True


def run(data):
    ou.setLogLevel(ou.LOG_NONE)
    ou.RNG.setSeed(data['seed'])
    space = ob.RealVectorStateSpace(3)
    bounds = ob.RealVectorBounds(3)
    for i in range(3):
        bounds.setLow(i, data['bounds']['xyz_min'][i]); bounds.setHigh(i, data['bounds']['xyz_max'][i])
    space.setBounds(bounds)
    ss = og.SimpleSetup(space)
    ss.setStateValidityChecker(lambda s: not any(collision(s, s, b) for b in data['boxes']))
    si = ss.getSpaceInformation()

    class PrismMotionValidator(ob.MotionValidator):
        def __init__(self, info):
            super().__init__(info)
        def checkMotion(self, a, b, *args):
            return not any(collision(a, b, box) for box in data['boxes'])
    validator = PrismMotionValidator(si)
    si.setMotionValidator(validator)
    start, goal = space.allocState(), space.allocState()
    for i in range(3):
        start[i], goal[i] = data['start'][i], data['goal'][i]
    ss.setStartAndGoalStates(start, goal)
    planner = og.BITstar(si)
    planner.setSamplesPerBatch(100)
    ss.setPlanner(planner)
    ss.solve(float(data['budget']))
    if not ss.haveExactSolutionPath():
        return {'path': []}
    path = ss.getSolutionPath()
    return {'path': [[float(s[i]) for i in range(3)] for s in path.getStates()]}


if __name__ == '__main__':
    print(json.dumps(run(json.load(sys.stdin))))
