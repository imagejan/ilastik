import numpy
import h5py
import vigra.analysis
import math

from lazyflow.graph import Operator, InputSlot, OutputSlot
from lazyflow.stype import Opaque
from lazyflow.rtype import SubRegion, List
from ilastik.applets.objectExtraction.opObjectExtraction import OpObjectExtraction



class OpDivisionFeatureExtraction(OpObjectExtraction):
    name = "Division Feature Extraction"

    RegionFeaturesVigra = OutputSlot(stype=Opaque, rtype=List)
        
    def __init__(self, parent):
        super(OpDivisionFeatureExtraction, self).__init__(parent)
        
        self.RegionFeaturesVigra.connect(self._opRegFeats.Output)
        
        self.RegionFeatures.disconnect()
            
        self._opDivFeats = OpDivisionFeatures(parent=self)
        self._opDivFeats.LabelImage.connect(self.LabelImage)
        self._opDivFeats.RawImage.connect(self.RawImage)
        self._opDivFeats.RegionFeaturesVigra.connect(self.RegionFeaturesVigra)
        
        self.RegionFeatures.connect(self._opDivFeats.DivisionFeatures)
        
        
    def setupOutputs(self):
        pass

    def execute(self, slot, subindex, roi, result):
        assert False, "Shouldn't get here."

    def propagateDirty(self, inputSlot, subindex, roi):
        pass



class OpDivisionFeatures(Operator):
    name = "Division Features"
    
    LabelImage = InputSlot()
    RawImage = InputSlot()
    RegionFeaturesVigra = InputSlot(stype=Opaque, rtype=List)
    
    DivisionFeatures = OutputSlot(stype=Opaque, rtype=List)
    
    divisionFeatures = ['SquaredDistance%02d', 'AngleDaughters', 'ChildrenSizeRatio',\
                         'SquaredDistanceRatio']
    numNeighbors = 3
    templateSize = 100 
    
    defaultSquaredDistance = 1000
    
    def __init__(self, parent):
        super(OpDivisionFeatures,self).__init__(parent=parent)
        self._cache = {}
        self.fixed = False
        
    def setupOutputs(self):
        self.DivisionFeatures.meta.assignFrom(self.RegionFeaturesVigra.meta)        

    def execute(self, slot, subindex, roi, result):
        assert slot == self.DivisionFeatures
        
        feats = {}
        if len(roi) == 0:
            roi = range(self.LabelImage.meta.shape[0])
        for t in roi:            
            if t in self._cache:
                # FIXME: if features have changed, they may not be in the cache.
                feats_at = self._cache[t]
            elif self.fixed:
                feats_at = dict((f, numpy.asarray([[]])) for f in self.features)
            else:
                feats_at = []
                lshape = self.LabelImage.meta.shape
                numChannels = lshape[-1]
                                
                region_feats_cur = self.RegionFeaturesVigra.get([t]).wait()[t]
                
                for c in range(numChannels):
                    feats_at_c = {} 
                    
                    for name in region_feats_cur[c].keys():
                        feats_at_c[name] = region_feats_cur[c][name]
                    
                    num = region_feats_cur[c]['RegionCenter'].shape[0]
                    
                    for n in range(self.numNeighbors):
                        if 'SquaredDistance%02d' in self.divisionFeatures:                            
                            name = 'SquaredDistance%02d' % n                            
                            self.divisionFeatures.append(name)
                            if n == self.numNeighbors - 1:
                                self.divisionFeatures.remove('SquaredDistance%02d')
                        name = 'SquaredDistance%02d' % n        
                        feats_at_c[name] = numpy.ones([num,1]) * self.defaultSquaredDistance
                    if 'AngleDaughters' in self.divisionFeatures:
                        feats_at_c['AngleDaughters'] = numpy.zeros([num,1])
                    if 'ChildrenSizeRatio' in self.divisionFeatures:
                        feats_at_c['ChildrenSizeRatio'] = numpy.zeros([num,1])
#                    if 'NumNeighborsDifference' in self.divisionFeatures:
#                        feats_at_c['NumNeighborsDifference'] = numpy.zeros([num,1])
                    if 'SquaredDistanceRatio' in self.divisionFeatures:
                        feats_at_c['SquaredDistanceRatio'] = numpy.zeros([num,1])
                        
                    if t < self.LabelImage.meta.shape[0] - 1:
                        region_feats_next = self.RegionFeaturesVigra.get([t+1]).wait()[t+1]
    
                        tcroi_next = SubRegion(self.LabelImage,
                                          start = [t+1,] + (len(lshape) - 2) * [0,] + [c,],
                                          stop = [t+2,] + list(lshape[1:-1]) + [c+1,])
    
                        image_next = self.RawImage.get(tcroi_next).wait()
                        axiskeys = self.RawImage.meta.getTaggedShape().keys()
                        assert axiskeys == list('txyzc'), "FIXME: OpRegionFeatures requires txyzc input data."
                        image_next = image_next[0,...,0] # assumes t,x,y,z,c
    
                        labels_next = self.LabelImage.get(tcroi_next).wait()
                        axiskeys = self.LabelImage.meta.getTaggedShape().keys()
                        assert axiskeys == list('txyzc'), "FIXME: OpRegionFeatures requires txyzc input data."
                        labels_next = labels_next[0,...,0] # assumes t,x,y,z,c
                        
                        self.extractDivisionFeatures(feats_at_c, region_feats_next[c], labels_next, self.divisionFeatures, numNeighbors=self.numNeighbors)
                    
                    feats_at.append(feats_at_c)    

                self._cache[t] = feats_at                
                self.DivisionFeatures._sig_value_changed()
            feats[t] = feats_at   
        return feats     
    
    
    def extractDivisionFeatures(self, feats_at_cur, feats_at_next, img_at_next, divFeatures, numNeighbors = 3):
        ''' adds division features to feats_at_cur '''        
        for label_cur, com_cur in enumerate(feats_at_cur['RegionCenter']):
            if label_cur == 0:
                continue
                    
            if len(img_at_next.shape) == 3: #txyc
                channel_axis = 3
            elif len(img_at_next.shape) == 4: #txyzc
                channel_axis = 4
            else:
                raise Exception("image shape not supported")
                        
            idx_cur = [round(x) for x in com_cur]
            
            roi = []
            for idx,coord in enumerate(idx_cur):
                if idx == channel_axis - 1:
                    assert(coord == 0., "RegionCenter has more dimensions than the image has")
                    continue
                start = coord - self.templateSize/2
                stop = coord + self.templateSize/2
                if start < 0:
                    start = 0
                if stop > img_at_next.shape[idx]:
                    stop = img_at_next.shape[idx]
                roi.append(slice(start,stop))
            
            roi.append(slice(0,1))  # channel
            
            # find all coms in the neighborhood of com_cur
            subimg_next = img_at_next[roi]
            labels_next = numpy.unique(subimg_next)
            coms_next = {}
            for l in labels_next:
                if l != 0:
                    coms_next[l] = feats_at_next['RegionCenter'][l]
                        
            sqDist = self.getSquaredDistances(com_cur, coms_next, numNeighbors)
            coms_next_reduced = {}
            for idx,row in enumerate(sqDist):
                l = row[0]
                dist = row[1]
                name = 'SquaredDistance%02d' % idx
                if name in divFeatures:
                    feats_at_cur[name][label_cur][0] = dist
                coms_next_reduced[l] = coms_next[l]
            
            if 'AngleDaughters' in divFeatures:
                feats_at_cur['AngleDaughters'][label_cur][0] = self.getMaxAngle(com_cur, coms_next_reduced)     
            
            if 'ChildrenSizeRatio' in divFeatures:
                sizes_next = []
                for label in coms_next_reduced.keys(): 
                    sizes_next.append(feats_at_next['Count'][label])
                feats_at_cur['ChildrenSizeRatio'][label_cur][0] = self.getChildrenSizeRatio(sizes_next)
            
            if 'SquaredDistanceRatio' in divFeatures:
                feats_at_cur['SquaredDistanceRatio'][label_cur][0] = self.getSquaredDistanceRatio(sqDist)
            
    
    def dotproduct(self, v1, v2):
        return sum((a*b) for a, b in zip(v1, v2))
    
    def length(self, v):
        return math.sqrt(self.dotproduct(v, v))
    
    def angle(self, v1, v2):
        radians = math.acos(self.dotproduct(v1, v2) / (self.length(v1) * self.length(v2)))
        return (radians*180)/math.pi
  
  
    def getMaxAngle(self, com_cur, coms_next):
        ''' returns the maximum angle between two potential children '''        
        angles = []
        for idx, key1 in enumerate(sorted(coms_next.keys())):
            com1 = coms_next[key1]
            v1 = com1 - com_cur
            for key2 in sorted(coms_next.keys())[idx+1:]:
                com2 = coms_next[key2]                
                v2 = com2 - com_cur                
                ang = self.angle(v1,v2)
                if ang > 180:
                    assert(ang<=360.01, "the angle must be smaller than 360 degrees")
                    ang = 360-ang
                angles.append(ang)
                    
        if len(angles) == 0:
            angles = [0]
        
        print 'max(angles) =', max(angles)
        return max(angles)

    
    def getSquaredDistances(self, com_cur, coms_next, num_best = 3, size_filter_from = 4):
        ''' returns the squared distances to the objects in the neighborhood of com_curr '''  
        squaredDistances = []
        
        for label_next in coms_next.keys():
            dist = numpy.linalg.norm(coms_next[label_next] - com_cur)
            squaredDistances.append([label_next,dist])
        
        squaredDistances = numpy.array(squaredDistances)
        # sort the array in the second column in ascending order
        squaredDistances = numpy.array(sorted(squaredDistances, key=lambda a_entry: a_entry[1]))
        if num_best > squaredDistances.shape[0]:
            num_best = squaredDistances.shape[0]
        
        if len(squaredDistances) == 0:
            print 'squaredDistances = ', []
            return []
        
        print 'squaredDistances = ', squaredDistances[0:num_best,:]
        return squaredDistances[0:num_best,:]
        
    
    def getChildrenSizeRatio(self, sizes_next):
        size_ratios = []
        for idx, size1 in enumerate(sizes_next):
            for size2 in sizes_next[idx+1:]:
                ratio = float(size1)/size2                
                if ratio > 1:
                    ratio = 1./ratio
                if math.isnan(ratio):
                    ratio = 0.
                size_ratios.append(ratio)
        if len(size_ratios) == 0:
            size_ratios.append(0)
        
        print 'childrenSizeRatio = ', max(size_ratios)
        return max(size_ratios)
    
    
    def getSquaredDistanceRatio(self, squaredDistances):
        dist_ratios = []
        
        for idx,row1 in enumerate(squaredDistances):            
            dist1 = row1[1]
            for row2 in squaredDistances[idx+1:]:
                dist2 = row2[1]
                ratio = float(dist1)/dist2                
                if ratio > 1:
                    ratio = 1./ratio
                if math.isnan(ratio):
                    ratio = 0.
                dist_ratios.append(ratio)
        if len(dist_ratios) == 0:
            dist_ratios.append(0)
        
        print 'squaredDistanceRatio = ', max(dist_ratios)
        return max(dist_ratios)
