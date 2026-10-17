# -*- coding: utf-8 -*-
"""
algorithm.py — vectorAlign

Pure computation layer. Contains NO QDockWidget / dialog UI code.
"""

import numpy as np
import traceback

from qgis.core import (
    QgsGeometry, QgsPoint, QgsPointXY, QgsWkbTypes, QgsTask,
    QgsLineString, QgsPolygon, QgsMultiPoint, QgsMultiLineString, QgsMultiPolygon
)
from qgis.PyQt.QtGui import QTransform


class TransformationEngine:
    @staticmethod
    def validate_control_points(source_pts, target_pts, method):
        min_points = {'similarity': 2, 'affine': 3, 'projective': 4, 'spline': 4, 'poly2': 6, 'poly3': 10}
        n = len(source_pts)
        required = min_points.get(method, 2)
        if n < required:
            return False, f"{method.capitalize()} requires at least {required} links. You have {n}."
        if n != len(target_pts):
            return False, "Source and target point counts don't match."
        return True, "Valid"

    @staticmethod
    def compute_polynomial_transform(source_pts, target_pts, order):
        n = len(source_pts)
        A, b_x, b_y = [], [], []
        for i in range(n):
            x, y = source_pts[i].x(), source_pts[i].y()
            tx, ty = target_pts[i].x(), target_pts[i].y()
            row = [1, x, y, x*y, x**2, y**2]
            if order == 3:
                row.extend([x**2*y, x*y**2, x**3, y**3])
            A.append(row)
            b_x.append(tx)
            b_y.append(ty)
        A, b_x, b_y = np.array(A), np.array(b_x), np.array(b_y)
        cx, _, _, _ = np.linalg.lstsq(A, b_x, rcond=None)
        cy, _, _, _ = np.linalg.lstsq(A, b_y, rcond=None)
        return {'type': f'poly{order}', 'coeffs_x': cx, 'coeffs_y': cy, 'order': order}

    @staticmethod
    def apply_polynomial_transform(point, params):
        x, y = point.x(), point.y()
        terms = [1, x, y, x*y, x**2, y**2]
        if params['order'] == 3:
            terms.extend([x**2*y, x*y**2, x**3, y**3])
        
        new_pt = QgsPoint(float(np.dot(terms, params['coeffs_x'])), float(np.dot(terms, params['coeffs_y'])))
        
        if params.get('preserve_z', False) and hasattr(point, 'is3D') and point.is3D():
            new_pt.addZValue(point.z())
        if params.get('preserve_m', False) and hasattr(point, 'isMeasure') and point.isMeasure():
            new_pt.addMValue(point.m())
        return new_pt

    @staticmethod
    def compute_similarity_transform(source_pts, target_pts):
        n = len(source_pts)
        src = np.array([[pt.x(), pt.y()] for pt in source_pts])
        tgt = np.array([[pt.x(), pt.y()] for pt in target_pts])
        src_c, tgt_c = np.mean(src, axis=0), np.mean(tgt, axis=0)
        src_norm, tgt_norm = src - src_c, tgt - tgt_c
        src_scale = np.sqrt(np.sum(src_norm**2) / n)
        
        if src_scale > 1e-9:
            scale = np.sqrt(np.sum(tgt_norm**2) / n) / src_scale
            src_norm /= src_scale
            tgt_norm /= (src_scale * scale)
        else:
            scale = 1.0
            
        U, S, Vt = np.linalg.svd(src_norm.T @ tgt_norm)
        R = Vt.T @ U.T
        if np.linalg.det(R) < 0:
            Vt[-1, :] *= -1
            R = Vt.T @ U.T
            
        return {'type': 'similarity', 'scale': scale, 'rotation': R, 'translation': tgt_c - scale * (R @ src_c)}

    @staticmethod
    def apply_similarity_transform(point, params):
        pt = np.array([point.x(), point.y()])
        t = params['scale'] * (params['rotation'] @ pt) + params['translation']
        new_pt = QgsPoint(float(t[0]), float(t[1]))
        
        if params.get('preserve_z', False) and hasattr(point, 'is3D') and point.is3D():
            new_pt.addZValue(point.z())
        if params.get('preserve_m', False) and hasattr(point, 'isMeasure') and point.isMeasure():
            new_pt.addMValue(point.m())
        return new_pt

    @staticmethod
    def compute_affine_transform(source_pts, target_pts):
        n = len(source_pts)
        A, b = [], []
        for i in range(n):
            x, y, tx, ty = source_pts[i].x(), source_pts[i].y(), target_pts[i].x(), target_pts[i].y()
            A.extend([[x, y, 1, 0, 0, 0], [0, 0, 0, x, y, 1]])
            b.extend([tx, ty])
        p, _, _, _ = np.linalg.lstsq(np.array(A), np.array(b), rcond=None)
        return {'type': 'affine', 'matrix': np.array([[p[0], p[1]], [p[3], p[4]]]), 'translation': np.array([p[2], p[5]])}

    @staticmethod
    def apply_affine_transform(point, params):
        t = params['matrix'] @ np.array([point.x(), point.y()]) + params['translation']
        new_pt = QgsPoint(float(t[0]), float(t[1]))
        
        if params.get('preserve_z', False) and hasattr(point, 'is3D') and point.is3D():
            new_pt.addZValue(point.z())
        if params.get('preserve_m', False) and hasattr(point, 'isMeasure') and point.isMeasure():
            new_pt.addMValue(point.m())
        return new_pt

    @staticmethod
    def compute_projective_transform(source_pts, target_pts):
        A = []
        for i in range(len(source_pts)):
            x, y, tx, ty = source_pts[i].x(), source_pts[i].y(), target_pts[i].x(), target_pts[i].y()
            A.extend([[x, y, 1, 0, 0, 0, -tx*x, -tx*y, -tx], [0, 0, 0, x, y, 1, -ty*x, -ty*y, -ty]])
        _, _, Vt = np.linalg.svd(np.array(A))
        H = Vt[-1, :].reshape(3, 3)
        return {'type': 'projective', 'homography': H / H[2, 2]}

    @staticmethod
    def apply_projective_transform(point, params):
        h = params['homography'] @ np.array([point.x(), point.y(), 1.0])
        if abs(h[2]) > 1e-10:
            new_pt = QgsPoint(float(h[0]/h[2]), float(h[1]/h[2]))
            if params.get('preserve_z', False) and hasattr(point, 'is3D') and point.is3D():
                new_pt.addZValue(point.z())
            if params.get('preserve_m', False) and hasattr(point, 'isMeasure') and point.isMeasure():
                new_pt.addMValue(point.m())
            return new_pt
        return QgsPoint(point.x(), point.y())

    @staticmethod
    def compute_tps_transform(source_pts, target_pts):
        n = len(source_pts)
        src = np.array([[pt.x(), pt.y()] for pt in source_pts])
        tgt = np.array([[pt.x(), pt.y()] for pt in target_pts])
        K = np.zeros((n, n))
        for i in range(n):
            for j in range(n):
                if i != j:
                    r_sq = np.sum((src[i] - src[j])**2)
                    if r_sq > 0:
                        K[i, j] = r_sq * np.log(r_sq)
        P = np.column_stack([np.ones(n), src])
        L = np.vstack([np.column_stack([K, P]), np.column_stack([P.T, np.zeros((3, 3))])])
        wx = np.linalg.lstsq(L, np.concatenate([tgt[:, 0], np.zeros(3)]), rcond=None)[0]
        wy = np.linalg.lstsq(L, np.concatenate([tgt[:, 1], np.zeros(3)]), rcond=None)[0]
        return {'type': 'tps', 'source_points': src, 'weights_x': wx, 'weights_y': wy, 'n_control': n}

    @staticmethod
    def apply_tps_transform(point, params):
        pt = np.array([point.x(), point.y()])
        src = params['source_points']
        rbf = np.array([np.sum((pt - s)**2) * np.log(np.sum((pt - s)**2)) if np.sum((pt - s)**2) > 0 else 0 for s in src])
        x = float(np.dot(np.concatenate([rbf, [1.0, pt[0], pt[1]]]), params['weights_x']))
        y = float(np.dot(np.concatenate([rbf, [1.0, pt[0], pt[1]]]), params['weights_y']))
        new_pt = QgsPoint(x, y)
        
        if params.get('preserve_z', False) and hasattr(point, 'is3D') and point.is3D():
            new_pt.addZValue(point.z())
        if params.get('preserve_m', False) and hasattr(point, 'isMeasure') and point.isMeasure():
            new_pt.addMValue(point.m())
        return new_pt

    @staticmethod
    def transform_geometry(geometry, params, method):
        if not geometry or geometry.isNull() or geometry.isEmpty():
            return geometry

        func = {
            'similarity': TransformationEngine.apply_similarity_transform,
            'affine': TransformationEngine.apply_affine_transform,
            'projective': TransformationEngine.apply_projective_transform,
            'spline': TransformationEngine.apply_tps_transform,
            'poly2': TransformationEngine.apply_polynomial_transform,
            'poly3': TransformationEngine.apply_polynomial_transform
        }.get(method)
        
        if not func:
            return geometry
        
        wkb_type = geometry.wkbType()
        base_type = QgsWkbTypes.geometryType(wkb_type)
        is_multi = QgsWkbTypes.isMultiType(wkb_type)
        has_z = QgsWkbTypes.hasZ(wkb_type) and params.get('preserve_z', False)
        has_m = QgsWkbTypes.hasM(wkb_type) and params.get('preserve_m', False)

        coord_cache = {}

        if not (has_z or has_m):
            def transform_pt_2d(pt):
                key = (round(pt.x(), 8), round(pt.y(), 8))
                if key not in coord_cache:
                    coord_cache[key] = func(pt, params)
                res = coord_cache[key]
                return QgsPointXY(res.x(), res.y())

            if base_type == QgsWkbTypes.GeometryType.PointGeometry:
                if is_multi:
                    return QgsGeometry.fromMultiPointXY([transform_pt_2d(pt) for pt in geometry.asMultiPoint()])
                else:
                    return QgsGeometry.fromPointXY(transform_pt_2d(geometry.asPoint()))

            elif base_type == QgsWkbTypes.GeometryType.LineGeometry:
                if is_multi:
                    return QgsGeometry.fromMultiPolylineXY([[transform_pt_2d(pt) for pt in line] for line in geometry.asMultiPolyline()])
                else:
                    return QgsGeometry.fromPolylineXY([transform_pt_2d(pt) for pt in geometry.asPolyline()])

            elif base_type == QgsWkbTypes.GeometryType.PolygonGeometry:
                if is_multi:
                    multi_poly_data = [[[transform_pt_2d(pt) for pt in ring] for ring in poly] for poly in geometry.asMultiPolygon()]
                    return QgsGeometry.fromMultiPolygonXY(multi_poly_data)
                else:
                    poly_data = [[transform_pt_2d(pt) for pt in ring] for ring in geometry.asPolygon()]
                    return QgsGeometry.fromPolygonXY(poly_data)

            return geometry

        geom_obj = geometry.constGet()
        if geom_obj is None:
            return geometry

        def transform_pt_zm(pt):
            key = (round(pt.x(), 8), round(pt.y(), 8))
            if key not in coord_cache:
                coord_cache[key] = func(pt, params)
            res = coord_cache[key]
            out = QgsPoint(res.x(), res.y())
            if has_z and hasattr(pt, 'is3D') and pt.is3D():
                out.addZValue(pt.z())
            if has_m and hasattr(pt, 'isMeasure') and pt.isMeasure():
                out.addMValue(pt.m())
            return out

        if base_type == QgsWkbTypes.GeometryType.PointGeometry:
            if is_multi:
                mp = QgsMultiPoint()
                for i in range(geom_obj.numGeometries()):
                    mp.addGeometry(transform_pt_zm(geom_obj.geometryN(i)))
                return QgsGeometry(mp)
            else:
                return QgsGeometry(transform_pt_zm(geom_obj))

        elif base_type == QgsWkbTypes.GeometryType.LineGeometry:
            if is_multi:
                mls = QgsMultiLineString()
                for i in range(geom_obj.numGeometries()):
                    ls = geom_obj.geometryN(i)
                    new_ls = QgsLineString([transform_pt_zm(ls.pointAt(j)) for j in range(ls.numPoints())])
                    mls.addGeometry(new_ls)
                return QgsGeometry(mls)
            else:
                ls = geom_obj
                new_ls = QgsLineString([transform_pt_zm(ls.pointAt(j)) for j in range(ls.numPoints())])
                return QgsGeometry(new_ls)

        elif base_type == QgsWkbTypes.GeometryType.PolygonGeometry:
            def _build_poly_zm(poly):
                new_poly = QgsPolygon()
                if poly.exteriorRing():
                    ext = poly.exteriorRing()
                    new_poly.setExteriorRing(QgsLineString([transform_pt_zm(ext.pointAt(j)) for j in range(ext.numPoints())]))
                for r in range(poly.numInteriorRings()):
                    hole = poly.interiorRing(r)
                    new_poly.addInteriorRing(QgsLineString([transform_pt_zm(hole.pointAt(j)) for j in range(hole.numPoints())]))
                return new_poly

            if is_multi:
                mpoly = QgsMultiPolygon()
                for i in range(geom_obj.numGeometries()):
                    mpoly.addGeometry(_build_poly_zm(geom_obj.geometryN(i)))
                return QgsGeometry(mpoly)
            else:
                return QgsGeometry(_build_poly_zm(geom_obj))

        return geometry

    @staticmethod
    def calculate_smart_transform(layers_dict, canvas):
        x_coords = []
        y_coords = []
        for layer in layers_dict.values():
            if not layer or not layer.isValid():
                continue
            for feat in layer.getFeatures():
                geom = feat.geometry()
                if not geom.isNull() and not geom.isEmpty():
                    pt = geom.boundingBox().center()
                    x_coords.append(pt.x())
                    y_coords.append(pt.y())
                    
        if not x_coords:
            return None, "No valid geometry found in layers."
        
        x_coords, y_coords = np.array(x_coords), np.array(y_coords)
        med_x, med_y = np.median(x_coords), np.median(y_coords)
        mad_x = np.median(np.abs(x_coords - med_x))
        mad_y = np.median(np.abs(y_coords - med_y))
        
        mad_x = mad_x if mad_x > 0 else 1.0
        mad_y = mad_y if mad_y > 0 else 1.0
        
        core_mask = (np.abs(x_coords - med_x) <= 3 * mad_x) & (np.abs(y_coords - med_y) <= 3 * mad_y)
        core_x, core_y = x_coords[core_mask], y_coords[core_mask]
        
        if len(core_x) == 0:
            core_x, core_y = x_coords, y_coords
            
        core_width = np.max(core_x) - np.min(core_x)
        core_height = np.max(core_y) - np.min(core_y)
        core_width = core_width if core_width > 0 else 1.0
        core_height = core_height if core_height > 0 else 1.0
        
        canvas_ext = canvas.extent()
        scale = min((canvas_ext.width() * 0.8) / core_width, (canvas_ext.height() * 0.8) / core_height)
        
        canvas_center = canvas_ext.center()
        transform = QTransform()
        transform.translate(canvas_center.x(), canvas_center.y())
        transform.scale(scale, scale)
        transform.translate(-med_x, -med_y)
        
        return transform, "Success"


class GeometryTransformTask(QgsTask):
    def __init__(self, description, layers_data, transform_engine, params, method, transform, callback):
        super().__init__(description, QgsTask.Flag.CanCancel)
        self.layers_data = layers_data
        self.transform_engine = transform_engine
        self.params = params
        self.method = method
        self.transform = transform
        self.callback = callback
        self.results = []
        self.error_msg = ""
        self._last_reported_progress = -1

    def run(self):
        try:
            total_features = sum(len(ldata['features']) for ldata in self.layers_data)
            if total_features == 0:
                return True
                
            processed = 0
            for ldata in self.layers_data:
                layer_result = {'layer_id': ldata['layer_id'], 'geometries': {}}
                
                for fid, geom in ldata['features'].items():
                    if self.isCanceled():
                        return False
                        
                    if self.transform:
                        new_geom = QgsGeometry(geom)
                        new_geom.transform(self.transform)
                    else:
                        new_geom = self.transform_engine.transform_geometry(geom, self.params, self.method)
                        
                    layer_result['geometries'][fid] = new_geom
                    processed += 1
                    
                    pct = (processed / total_features) * 80
                    if int(pct) > self._last_reported_progress:
                        self.setProgress(int(pct))
                        self._last_reported_progress = int(pct)
                    
                self.results.append(layer_result)
            return True
            
        except Exception as e:
            self.error_msg = traceback.format_exc()
            return False

    def finished(self, result):
        if result:
            self.callback(True, self.results, "")
        else:
            if self.isCanceled():
                self.callback(False, [], "Canceled")
            else:
                self.callback(False, [], self.error_msg)


class AttributeTransferTask(QgsTask):
    def __init__(self, description, source_data, target_data, transform_engine, params, method, crs_src, crs_tgt, mappings, callback):
        super().__init__(description, QgsTask.Flag.CanCancel)
        self.source_data = source_data
        self.target_data = target_data
        self.transform_engine = transform_engine
        self.params = params
        self.method = method
        self.crs_src = crs_src
        self.crs_tgt = crs_tgt
        self.mappings = mappings
        self.callback = callback
        self.results = {}
        self.unmatched_count = 0
        self.error_msg = ""
        self._last_reported_progress = -1

    def run(self):
        try:
            from qgis.core import QgsSpatialIndex, QgsFeature, QgsCoordinateTransform, QgsCoordinateReferenceSystem, QgsCoordinateTransformContext
            
            crs_transform = None
            if self.crs_src and self.crs_tgt and self.crs_src != self.crs_tgt:
                src_crs = QgsCoordinateReferenceSystem(self.crs_src)
                tgt_crs = QgsCoordinateReferenceSystem(self.crs_tgt)
                crs_transform = QgsCoordinateTransform(src_crs, tgt_crs, QgsCoordinateTransformContext())
            
            tgt_index = QgsSpatialIndex()
            for tgt_id, geom in self.target_data.items():
                if self.isCanceled():
                    return False
                feat = QgsFeature(tgt_id)
                feat.setGeometry(geom)
                tgt_index.insertFeature(feat)
            
            total = len(self.source_data)
            if total == 0:
                return True
            
            processed = 0
            for src_id, geom, attributes in self.source_data:
                if self.isCanceled():
                    return False
                
                transformed_geom = self.transform_engine.transform_geometry(geom, self.params, self.method)
                
                if crs_transform:
                    transformed_geom.transform(crs_transform)
                    
                bbox = transformed_geom.boundingBox()
                intersecting_ids = tgt_index.intersects(bbox)
                
                best_match_id = None
                for tgt_id in intersecting_ids:
                    if tgt_id in self.target_data and self.target_data[tgt_id].intersects(transformed_geom):
                        best_match_id = tgt_id
                        break
                        
                if best_match_id is None:
                    centroid = transformed_geom.centroid().asPoint()
                    nearest_ids = tgt_index.nearestNeighbor(centroid, 1)
                    if nearest_ids:
                        best_match_id = nearest_ids[0]
                    
                if best_match_id is not None:
                    if best_match_id not in self.results:
                        self.results[best_match_id] = {}
                    for src_field, tgt_field in self.mappings:
                        self.results[best_match_id][tgt_field] = attributes.get(src_field)
                else:
                    self.unmatched_count += 1
                    
                processed += 1
                pct = (processed / total) * 100
                if int(pct) > self._last_reported_progress:
                    self.setProgress(int(pct))
                    self._last_reported_progress = int(pct)
                    
            return True
        except Exception as e:
            self.error_msg = traceback.format_exc()
            return False

    def finished(self, result):
        if result:
            self.callback(True, self.results, self.unmatched_count, "")
        else:
            if self.isCanceled():
                self.callback(False, {}, 0, "Canceled")
            else:
                self.callback(False, {}, 0, self.error_msg)