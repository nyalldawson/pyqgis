# -*- coding: utf-8 -*-
###############################################################################
#
# Copyright (C) 2016 Nyall Dawson, SMEC
#
# This source is free software; you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version.
#
# This code is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
###############################################################################

import os.path
import re
import sip
from qgis.core import (QgsLayerTreeGroup,
                       QgsLayerTreeLayer,
                       QgsProject,
                       QgsFields,
                       QgsField,
                       QgsFeature,
                       QgsDataProvider,
                       QgsFeatureSink,
                       QgsGeometry,
                       QgsRectangle,
                       QgsRasterPipe,
                       QgsFeatureRequest,
                       QgsRasterBlockFeedback,
                       QgsVectorLayer,
                       QgsRasterFileWriter,
                       QgsWkbTypes,
                       QgsLayoutItemMap,
                       QgsLayoutItemRegistry,
                       QgsProcessing,
                       QgsProcessingException,
                       QgsProcessingAlgorithm,
                       QgsProcessingParameterRasterLayer,
                       QgsProcessingParameterFolderDestination,
                       QgsProcessingParameterFeatureSource,
                       QgsProcessingParameterVectorLayer,
                       QgsProcessingParameterBoolean,
                       QgsProcessingParameterCrs,
                       QgsProcessingParameterNumber,
                       QgsProcessingParameterPoint,
                       QgsProcessingParameterFileDestination,
                       QgsProcessingParameterFeatureSink,
                       QgsProcessingParameterString)
from qgis.gui import QgsLayoutItemComboBox

from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtWidgets import QComboBox

from processing.gui.wrappers import (
    WidgetWrapper
)


class PrintLayoutWidgetWrapper(WidgetWrapper):
    """
    WidgetWrapper for QgsProcessingParameterString that create and manage a combobox widget
    filled with current project print layouts
    """

    def createWidget(self):
        self._combo = QComboBox()
        manager = QgsProject.instance().layoutManager()
        for layout in manager.printLayouts():
            self._combo.addItem(layout.name())

        self._combo.currentIndexChanged.connect(lambda: self.widgetValueHasChanged.emit(self))
        return self._combo

    def setValue(self, value):
        self.setComboValue(value, self._combo)

    def value(self):
        return self.comboValue(combobox=self._combo)


class PrintLayoutMapWidgetWrapper(WidgetWrapper):

    def createWidget(self, layout_param=None):
        self._layout_param = layout_param
        self._layout_name = None

        self._combo = QgsLayoutItemComboBox()
        self._combo.setItemType(QgsLayoutItemRegistry.LayoutMap)

        # TODO -- overwrite layout item mode, hiding selected status, sort by name (not z-order)

        self._combo.currentIndexChanged.connect(lambda: self.widgetValueHasChanged.emit(self))
        return self._combo

    def postInitialize(self, wrappers):
        for wrapper in wrappers:
            if wrapper.param.name() == self._layout_param:
                self.layout_wrapper = wrapper
                self.set_layout_name(wrapper.value())
                wrapper.widgetValueHasChanged.connect(self.layoutChanged)

    def layoutChanged(self, wrapper):
        layout_name = wrapper.value()
        if layout_name == self._layout_name:
            return
        self.set_layout_name(layout_name)

    def set_layout_name(self, layout_name):
        self._layout_name = layout_name
        self.refreshItems()
        self.widgetValueHasChanged.emit(self)

    def refreshItems(self):
        value = self.comboValue(combobox=self._combo)
        if self._layout_name:
            layout = QgsProject.instance().layoutManager().layoutByName(self._layout_name)
            self._combo.setCurrentLayout(layout)
        else:
            self._combo.setCurrentLayout(None)

        # TODO - modeler
        #        if self.dialogType == DIALOG_MODELER:
        #           strings = self.dialog.getAvailableValuesOfType(
        #              [QgsProcessingParameterString, QgsProcessingParameterNumber, QgsProcessingParameterFile,
        #              QgsProcessingParameterField, QgsProcessingParameterExpression], QgsProcessingOutputString)
        #        for text, data in [(self.dialog.resolveValueDescription(s), s) for s in strings]:
        #           self._combo.addItem(text, data)

        self.setComboValue(value, self._combo)

    def setValue(self, value):
        self.setComboValue(value, self._combo)
        self.widgetValueHasChanged.emit(self)

    def value(self):
        item = self._combo.currentItem()
        if item is None:
            return None

        return item.uuid()


class CreateAtlasFeature(QgsProcessingAlgorithm):
    LAYOUT = 'LAYOUT'
    MAP = 'MAP'
    SCALE = 'SCALE'
    ORIGIN = 'ORIGIN'
    CRS = 'CRS'

    OUTPUT = 'OUTPUT'

    def createInstance(self):
        return CreateAtlasFeature()

    def name(self):
        return 'createatlasfeaturetemplate'

    def displayName(self):
        return 'Create atlas feature template'

    def group(self):
        return 'Cartography'

    def groupId(self):
        return 'cartography'

    def shortHelpString(self):
        return "Creates a single polygon feature corresponding to a map extent given a scale and map height/width."

    def shortDescription(self):
        return "Create a polygon feature template for atlas generation."

    def initAlgorithm(self, config=None):
        param = QgsProcessingParameterString(self.LAYOUT, 'Print layout')
        param.setMetadata({
            'widget_wrapper': {
                'class': PrintLayoutWidgetWrapper}})
        self.addParameter(param)

        map_param = QgsProcessingParameterString(self.MAP, 'Map item')
        map_param.setMetadata({
            'widget_wrapper': {
                'class': PrintLayoutMapWidgetWrapper,
                'layout_param': 'LAYOUT'}})
        self.addParameter(map_param)

        self.addParameter(QgsProcessingParameterNumber(self.SCALE,
                                                       'Target scale', QgsProcessingParameterNumber.Double,
                                                       10000, False, 1, 10000000))
        self.addParameter(QgsProcessingParameterPoint(self.ORIGIN,
                                                      'Origin'))
        self.addParameter(QgsProcessingParameterCrs(
            self.CRS, 'Override CRS (if blank, map CRS will be used)', optional=True))

        self.addParameter(QgsProcessingParameterFeatureSink(self.OUTPUT,
                                                            'Output layer', QgsProcessing.TypeVectorPolygon))

    def processAlgorithm(self, parameters, context, feedback):
        layout_name = self.parameterAsString(parameters, self.LAYOUT, context)
        map_uuid = self.parameterAsString(parameters, self.MAP, context)

        layout = context.project().layoutManager().layoutByName(layout_name)
        if layout is None:
            raise QgsProcessingException('Cannot find layout with name "{}"'.format(layout_name))

        item = layout.itemByUuid(map_uuid)
        if item is None:
            raise QgsProcessingException('Cannot find matching map item with uuid "{}"'.format(map_uuid))
        map = sip.cast(item, QgsLayoutItemMap)

        target_crs = self.parameterAsCrs(parameters, self.CRS, context)
        if not target_crs.isValid():
            target_crs = map.crs()

        fields = QgsFields()
        fields.append(QgsField('width', QVariant.Double))
        fields.append(QgsField('height', QVariant.Double))
        fields.append(QgsField('scale', QVariant.Double))

        sink, dest = self.parameterAsSink(parameters, self.OUTPUT, context, fields, QgsWkbTypes.Polygon, target_crs)
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))

        width = map.rect().width()
        height = map.rect().height()
        scale = self.parameterAsDouble(parameters, self.SCALE, context)

        # blindly assume meters for now!
        feature_width = width / 1000.0 * scale
        feature_height = height / 1000.0 * scale
        origin = self.parameterAsPoint(parameters, self.ORIGIN, context)
        origin_x = origin.x()
        origin_y = origin.y()

        feat = QgsFeature(fields)
        feat.setAttributes([width, height, scale])
        feat.setGeometry(
            QgsGeometry.fromRect(QgsRectangle(origin_x, origin_y - feature_height, origin_x + feature_width, origin_y)))
        sink.addFeature(feat, QgsFeatureSink.FastInsert)

        return {self.OUTPUT: dest}


class LayoutMapExtentToLayer(QgsProcessingAlgorithm):
    LAYOUT = 'LAYOUT'
    MAP = 'MAP'
    OUTPUT = 'OUTPUT'

    def createInstance(self):
        return LayoutMapExtentToLayer()

    def name(self):
        return 'printlayoutmapextenttolayer'

    def displayName(self):
        return 'Print layout map extent to layer'

    def group(self):
        return 'Cartography'

    def groupId(self):
        return 'cartography'

    def shortHelpString(self):
        return "Creates a polygon layer containing the extent of a print layout map item, with attributes specifying the map size, scale and rotatation."

    def shortDescription(self):
        return "Creates a polygon layer containing the extent of a print layout map item"

    def initAlgorithm(self, config=None):
        param = QgsProcessingParameterString(self.LAYOUT, 'Print layout')
        param.setMetadata({
            'widget_wrapper': {
                'class': PrintLayoutWidgetWrapper}})
        self.addParameter(param)

        map_param = QgsProcessingParameterString(self.MAP, 'Map item')
        map_param.setMetadata({
            'widget_wrapper': {
                'class': PrintLayoutMapWidgetWrapper,
                'layout_param': 'LAYOUT'}})
        self.addParameter(map_param)

        self.addParameter(QgsProcessingParameterFeatureSink(self.OUTPUT,
                                                            'Layout map extent', QgsProcessing.TypeVectorPolygon))

    def processAlgorithm(self, parameters, context, feedback):
        layout_name = self.parameterAsString(parameters, self.LAYOUT, context)
        map_uuid = self.parameterAsString(parameters, self.MAP, context)

        layout = context.project().layoutManager().layoutByName(layout_name)
        if layout is None:
            raise QgsProcessingException('Cannot find layout with name "{}"'.format(layout_name))

        item = layout.itemByUuid(map_uuid)
        if item is None:
            raise QgsProcessingException('Cannot find matching map item with uuid "{}"'.format(map_uuid))
        map = sip.cast(item, QgsLayoutItemMap)

        fields = QgsFields()
        fields.append(QgsField('width', QVariant.Double))
        fields.append(QgsField('height', QVariant.Double))
        fields.append(QgsField('scale', QVariant.Double))
        fields.append(QgsField('rotation', QVariant.Double))

        sink, dest = self.parameterAsSink(parameters, self.OUTPUT, context, fields, QgsWkbTypes.Polygon, map.crs())
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))

        extent = QgsGeometry.fromQPolygonF(map.visibleExtentPolygon())
        f = QgsFeature()
        f.setAttributes([map.rect().width(),
                         map.rect().height(),
                         map.scale(),
                         map.mapRotation()])
        f.setGeometry(extent)
        sink.addFeature(f, QgsFeatureSink.FastInsert)

        return {self.OUTPUT: dest}
