###############################################################################
#   ilastik: interactive learning and segmentation toolkit
#
#       Copyright (C) 2011-2014, the ilastik developers
#                                <team@ilastik.org>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# In addition, as a special exception, the copyright holders of
# ilastik give you permission to combine ilastik with applets,
# workflows and plugins which are not covered under the GNU
# General Public License.
#
# See the LICENSE file for details. License information is also available
# on the ilastik web site at:
#          http://ilastik.org/license.html
###############################################################################
import os
import logging
from functools import partial
from collections import OrderedDict

import numpy
import yaml

from PyQt5 import uic
from PyQt5.QtCore import Qt, pyqtSlot, QTimer
from PyQt5.QtGui import QColor, QIcon
from PyQt5.QtWidgets import QStackedWidget, QFileDialog, QMenu, QLineEdit, \
                            QVBoxLayout, QDialog, QGridLayout, QLabel, \
                            QPushButton, QHBoxLayout, QDesktopWidget, QComboBox

from ilastik.applets.networkClassification.tiktorchWizard import MagicWizard
from ilastik.applets.labeling.labelingGui import LabelingGui
from ilastik.utility.gui import threadRouted
from ilastik.utility import bind
from ilastik.shell.gui.iconMgr import ilastikIcons

from volumina.api import LazyflowSource, AlphaModulatedLayer, GrayscaleLayer
from volumina.utility import PreferencesManager

from lazyflow.classifiers import TikTorchLazyflowClassifierFactory

logger = logging.getLogger(__name__)

def _listReplace(old, new):
    if len(old) > len(new):
        return new + old[len(new):]
    else:
        return new


class ParameterDlg(QDialog):
    """
    simple window for setting parameters
    """

    def __init__(self, parent):
        self.hparams = None

        super(QDialog, self).__init__(parent=parent)

        self.optimizer_combo = QComboBox(self)
        self.optimizer_combo.addItem("Adam")
        self.optimizer_kwargs_textbox = QLineEdit()
        self.optimizer_kwargs_textbox.setPlaceholderText("e.g. for Adam: {'lr': 0.0003, 'weight_decay':0.0001, amsgrad: True}")
        
        self.criterion_combo = QComboBox(self)
        self.criterion_combo.addItem("BCEWithLogitsLoss")
        self.criterion_kwargs_textbox = QLineEdit()
        self.criterion_kwargs_textbox.setPlaceholderText("e.g.: {'reduce': False}")
        
        self.batch_size_textbox = QLineEdit()
        self.batch_size_textbox.setPlaceholderText("default: 1")

        grid = QGridLayout()
        grid.setSpacing(10)

        grid.addWidget(QLabel('Optimizer'), 1, 0)
        grid.addWidget(self.optimizer_combo, 1, 1)

        grid.addWidget(QLabel('Optimizer keywork arguments'), 2, 0)
        grid.addWidget(self.optimizer_kwargs_textbox, 2, 1)

        grid.addWidget(QLabel('Criterion'), 3, 0)
        grid.addWidget(self.criterion_combo, 3, 1)

        grid.addWidget(QLabel('Criterion keyword arguments'), 4, 0)
        grid.addWidget(self.criterion_kwargs_textbox, 4, 1)

        grid.addWidget(QLabel('Batch size'), 5, 0)
        grid.addWidget(self.batch_size_textbox, 5, 1)

        okButton = QPushButton("OK")
        okButton.clicked.connect(self.readParameters)
        cancelButton = QPushButton("Cancel")
        cancelButton.clicked.connect(self.close)

        hbox = QHBoxLayout()
        hbox.addStretch(1)
        hbox.addWidget(okButton)
        hbox.addWidget(cancelButton)

        vbox = QVBoxLayout()
        vbox.addLayout(grid)
        vbox.addLayout(hbox)

        self.setLayout(vbox)

        # self.resize(480, 200)
        self.setFixedSize(600, 200)
        self.center()

        self.setWindowTitle('Hyperparameter Settings')
        self.show()


    def center(self):
        qr = self.frameGeometry()
        cp = QDesktopWidget().availableGeometry().center()
        qr.moveCenter(cp)

        self.move(qr.topLeft())

    def readParameters(self):
        optimizer = self.optimizer_combo.currentText()
        optimizer_kwargs = yaml.load(self.optimizer_kwargs_textbox.text())
        if optimizer_kwargs is None:
            optimizer_kwargs = dict(lr=0.0003, weight_decay=0.0001, amsgrad=True)
            optimizer = 'Adam'
        criterion = self.criterion_combo.currentText()
        criterion_kwargs = yaml.load(self.criterion_kwargs_textbox.text())
        if criterion_kwargs is None:
            criterion_kwargs = dict(reduce=False)
            criterion = 'BCEWithLogitsLoss'
        batch_size = int(self.batch_size_textbox.text()) if len(self.batch_size_textbox.text()) > 0 else 1

        self.hparams = dict(optimizer_kwargs=optimizer_kwargs,
                            optimizer_name=optimizer,
                            criterion_kwargs=criterion_kwargs,
                            criterion_name=criterion,
                            batch_size=batch_size)

        self.close()


class NNClassGui(LabelingGui):
    """
    LayerViewerGui class for Neural Network Classification
    """

    def viewerControlWidget(self):
        """
        Return the widget that controls how the content of the central widget is displayed
        """
        return self._viewerControlUi

    def centralWidget(self):
        """
        Return the widget that will be displayed in the main viewer area.
        """
        return self

    def stopAndCleanUp(self):
        """
        The gui should stop updating all data views and should clean up any resources it created
        """
        for fn in self.__cleanup_fns:
            fn()

        super(NNClassGui, self).stopAndCleanUp()

    def menus(self):
        """
        Return a list of QMenu widgets to be shown in the menu bar when this applet is visible
        """
        menus = super(NNClassGui, self).menus()

        advanced_menu = QMenu("TikTorch", parent=self)

        def settingParameter():
            """
            changing BatchSize 
            """
            dlg = ParameterDlg(parent=self)
            dlg.exec_()

            self.topLevelOperatorView.send_hparams(dlg.hparams)

        set_parameter = advanced_menu.addAction("Set hyperparameters")
        set_parameter.triggered.connect(settingParameter)

        def object_wizard():
            wizard = MagicWizard()
            wizard.show()
            wizard.exec_()

        advanced_menu.addAction("Create TikTorch configuration").triggered.connect(object_wizard)

        menus += [advanced_menu]

        return menus

    def __init__(self, parentApplet, topLevelOperatorView, labelingDrawerUiPath=None):
        labelSlots = LabelingGui.LabelingSlots()
        labelSlots.labelInput = topLevelOperatorView.LabelInputs
        labelSlots.labelOutput = topLevelOperatorView.LabelImages
        labelSlots.labelEraserValue = topLevelOperatorView.opLabelPipeline.opLabelArray.eraser
        labelSlots.labelDelete = topLevelOperatorView.opLabelPipeline.DeleteLabel
        labelSlots.labelNames = topLevelOperatorView.LabelNames

        if labelingDrawerUiPath is None:
            localDir = os.path.split(__file__)[0]
            labelingDrawerUiPath = os.path.join(localDir, "nnClassAppletUiTest.ui")

        super(NNClassGui, self).__init__(parentApplet, labelSlots, topLevelOperatorView, labelingDrawerUiPath)

        self.parentApplet = parentApplet
        self.classifiers = OrderedDict()

        self.liveTraining = False
        self.livePrediction = False

        self.__cleanup_fns = []

        self.labelingDrawerUi.liveTraining.setEnabled(False)
        self.labelingDrawerUi.liveTraining.setIcon(QIcon(ilastikIcons.System))
        self.labelingDrawerUi.liveTraining.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.labelingDrawerUi.liveTraining.toggled.connect(self.toggleLiveTraining)

        self.labelingDrawerUi.livePrediction.setEnabled(False)
        self.labelingDrawerUi.livePrediction.setIcon(QIcon(ilastikIcons.Play))
        self.labelingDrawerUi.livePrediction.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.labelingDrawerUi.livePrediction.toggled.connect(self.toggleLivePrediction)

        self.labelingDrawerUi.comboBox.clear()
        self.labelingDrawerUi.comboBox.hide()  # atm only a single model is supported
        self.labelingDrawerUi.addModel.clicked.connect(self.addModel)

        if self.topLevelOperatorView.ModelPath.ready():
            modelPathList = list(self.topLevelOperatorView.ModelPath.value.values())
            #TODO: save more networks
            # for modelPath in modelPathList:
            #     self.add_NN_classifiers(modelPath)
            self.add_NN_classifiers(modelPathList[0])

        self.initViewerControls()
        self.initViewerControlUi()

        self.labelingDrawerUi.labelListView.support_merges = True
        self.batch_size = self.topLevelOperatorView.Batch_Size.value
        num_label_classes = self._labelControlUi.labelListModel.rowCount()
        self.labelingDrawerUi.labelListView.allowDelete = num_label_classes > self.minLabelNumber
        self.labelingDrawerUi.AddLabelButton.setEnabled((num_label_classes < self.maxLabelNumber))

        def FreezePredDirty():
            self.toggleLivePrediction(not self.topLevelOperatorView.FreezePredictions.value)

        self.topLevelOperatorView.FreezePredictions.notifyDirty(bind(FreezePredDirty))
        self.__cleanup_fns.append(
            partial(self.topLevelOperatorView.FreezePredictions.unregisterDirty, bind(FreezePredDirty))
        )

        self.topLevelOperatorView.LabelNames.notifyDirty(bind(self.handleLabelSelectionChange))
        self.__cleanup_fns.append(
            partial(self.topLevelOperatorView.LabelNames.unregisterDirty, bind(self.handleLabelSelectionChange))
        )

        self.forceAtLeastTwoLabels(True)

        self.invalidatePredictionsTimer = QTimer()
        self.invalidatePredictionsTimer.timeout.connect(self.updatePredictions)

    def updatePredictions(self, forceDirty=False):
        if forceDirty:
            self.topLevelOperatorView.classifier_cache.Output.setDirty()
        else:
            pmap_source = self.topLevelOperatorView.PredictionProbabilities.upstream_slot
            while pmap_source.upstream_slot is not None:
                pmap_source = pmap_source.upstream_slot

            pmap_source.setValue(pmap_source[:].wait() * .9)


    def initViewerControls(self):
        """
        initializing viewerControl
        """
        self._viewerControlWidgetStack = QStackedWidget(parent=self)

    def initViewerControlUi(self):
        """
        Load the viewer controls GUI, which appears below the applet bar.
        In our case, the viewer control GUI consists mainly of a layer list.
        """
        localDir = os.path.split(__file__)[0]
        self._viewerControlUi = uic.loadUi(os.path.join(localDir, "viewerControls.ui"))

        def nextCheckState(checkbox):
            """
            sets the checkbox to the next state
            """
            checkbox.setChecked(not checkbox.isChecked())

        self._viewerControlUi.checkShowPredictions.nextCheckState = partial(
            nextCheckState, self._viewerControlUi.checkShowPredictions
        )
        self._viewerControlUi.checkShowPredictions.clicked.connect(self.handleShowPredictionsClicked)

        model = self.editor.layerStack
        self._viewerControlUi.viewerControls.setupConnections(model)

    def setupLayers(self):
        """
        which layers will be shown in the layerviewergui.
        Triggers the prediciton by setting the layer on visible
        """

        layers = super(NNClassGui, self).setupLayers()

        labels = self.labelListData

        for channel, predictionSlot in enumerate(self.topLevelOperatorView.PredictionProbabilityChannels):
            logger.info(f"prediction_slot: {predictionSlot}")
            if predictionSlot.ready() and channel < len(labels):
                ref_label = labels[channel]
                predictsrc = LazyflowSource(predictionSlot)
                predictionLayer = AlphaModulatedLayer(
                    predictsrc, tintColor=ref_label.pmapColor(), range=(0.0, 1.0), normalize=(0.0, 1.0)
                )
                predictionLayer.visible = self.labelingDrawerUi.livePrediction.isChecked()
                predictionLayer.opacity = 0.25
                predictionLayer.visibleChanged.connect(self.updateShowPredictionCheckbox)

                def setLayerColor(c, predictLayer_=predictionLayer, initializing=False):
                    if not initializing and predictLayer_ not in self.layerstack:
                        # This layer has been removed from the layerstack already.
                        # Don't touch it.
                        return
                    predictLayer_.tintColor = c

                def setPredLayerName(n, predictLayer_=predictionLayer, initializing=False):
                    """
                    function for setting the names for every Channel
                    """
                    if not initializing and predictLayer_ not in self.layerstack:
                        # This layer has been removed from the layerstack already.
                        # Don't touch it.
                        return
                    newName = "Prediction for %s" % n
                    predictLayer_.name = newName

                setPredLayerName(channel, initializing=True)
                setPredLayerName(ref_label.name, initializing=True)
                ref_label.pmapColorChanged.connect(setLayerColor)
                ref_label.nameChanged.connect(setPredLayerName)
                layers.append(predictionLayer)

        # Add the raw data last (on the bottom)
        inputDataSlot = self.topLevelOperatorView.InputImages
        if inputDataSlot.ready():
            inputLayer = self.createStandardLayerFromSlot(inputDataSlot)
            inputLayer.name = "Input Data"
            inputLayer.visible = True
            inputLayer.opacity = 1.0
            # the flag window_leveling is used to determine if the contrast
            # of the layer is adjustable
            if isinstance(inputLayer, GrayscaleLayer):
                inputLayer.window_leveling = True
            else:
                inputLayer.window_leveling = False

            def toggleTopToBottom():
                index = self.layerstack.layerIndex(inputLayer)
                self.layerstack.selectRow(index)
                if index == 0:
                    self.layerstack.moveSelectedToBottom()
                else:
                    self.layerstack.moveSelectedToTop()

            layers.append(inputLayer)

            # The thresholding button can only be used if the data is displayed as grayscale.
            if inputLayer.window_leveling:
                self.labelingDrawerUi.thresToolButton.show()
            else:
                self.labelingDrawerUi.thresToolButton.hide()

        self.handleLabelSelectionChange()

        return layers

    def toggleLivePrediction(self, checked):
        assert self.topLevelOperatorView.ClassifierFactory.ready()
        logger.debug(f'toggling live prediction mode to {checked}')

        # If we're changing modes, enable/disable our controls and other applets accordingly
        if self.livePrediction != checked:
            if checked:
                self.labelingDrawerUi.labelListView.allowDelete = False
                self.labelingDrawerUi.AddLabelButton.setEnabled(False)
            else:
                num_label_classes = self._labelControlUi.labelListModel.rowCount()
                self.labelingDrawerUi.labelListView.allowDelete = (num_label_classes > self.minLabelNumber)
                self.labelingDrawerUi.AddLabelButton.setEnabled((num_label_classes < self.maxLabelNumber))

        self.livePrediction = checked

        self.topLevelOperatorView.FreezePredictions.setValue(not checked)
        self.labelingDrawerUi.livePrediction.setChecked(checked)

        # Auto-set the "show predictions" state according to what the user just clicked.
        if checked:
            self._viewerControlUi.checkShowPredictions.setChecked(True)
            self.handleShowPredictionsClicked()

        # Notify the workflow that some applets may have changed state now.
        # (For example, the downstream pixel classification applet can
        #  be used now that there are features selected)
        self.parentApplet.appletStateUpdateRequested()

    def toggleLiveTraining(self, checked):
        assert self.topLevelOperatorView.ClassifierFactory.ready()

        if self.liveTraining != checked:
            model = self.topLevelOperatorView.ClassifierFactory[:].wait()[0]
            model.train_model = checked
            if checked:
                self.toggleLivePrediction(True)
                model.resume_training_process()
                self.invalidatePredictionsTimer.start(20000)  # start updating regularly
            else:
                model.pause_training_process()
                self.invalidatePredictionsTimer.stop()
                self.updatePredictions(True)  # update one last time

            self.liveTraining = checked

    @pyqtSlot()
    def handleShowPredictionsClicked(self):
        """
        sets the layer visibility when showPredicition is clicked
        """
        checked = self._viewerControlUi.checkShowPredictions.isChecked()
        for layer in self.layerstack:
            if "Prediction" in layer.name:
                layer.visible = checked

    @pyqtSlot()
    def updateShowPredictionCheckbox(self):
        """
        updates the showPrediction Checkbox when Predictions were added to the layers
        """
        predictLayerCount = 0
        visibleCount = 0
        for layer in self.layerstack:
            if "Prediction" in layer.name:
                predictLayerCount += 1
                if layer.visible:
                    visibleCount += 1

        if visibleCount == 0:
            self._viewerControlUi.checkShowPredictions.setCheckState(Qt.Unchecked)
        elif predictLayerCount == visibleCount:
            self._viewerControlUi.checkShowPredictions.setCheckState(Qt.Checked)
        else:
            self._viewerControlUi.checkShowPredictions.setCheckState(Qt.PartiallyChecked)

    def addModel(self):
        """
        When AddModel button is clicked.
        """
        # open dialog in recent model folder if possible
        folder = PreferencesManager().get('DataSelection', 'recent model')
        if folder is None:
            folder = os.path.expanduser('~')

        # get folder from user
        folder = self.getFolderToOpen(self, folder)

        if folder:
            # user did not cancel selection
            self.labelingDrawerUi.addModel.setEnabled(False)
            self.add_NN_classifiers(folder)
            PreferencesManager().set('DataSelection', 'recent model', folder)
            # disable adding another model TODO: handle new model in add_NN_Classifier
            self.labelingDrawerUi.addModel.setToolTip('Switching network model currently not supported.')
            # self.labelingDrawerUi.addModel.setEnabled(True)
            # self.labelingDrawerUi.addModel.setChecked(False)

    def add_NN_classifiers(self, folder_path):
        """
        Adds the chosen FilePath to the classifierDictionary and to the ComboBox
        """
        modelname = os.path.basename(os.path.normpath(folder_path))
        self.labelingDrawerUi.addModel.setText(f'{modelname} loaded')
        self.classifiers[modelname] = folder_path # Misleading! this attribute is an OrderedDict and contains the path to the TikTorch config and not the classifier/network itself.

        # clear first the comboBox or addItems will duplicate names
        self.labelingDrawerUi.comboBox.clear()
        self.labelingDrawerUi.comboBox.addItems(self.classifiers)
        self.labelingDrawerUi.liveTraining.setEnabled(True)
        self.labelingDrawerUi.livePrediction.setEnabled(True)


        self.topLevelOperatorView.set_classifier(folder_path)


    def getFolderToOpen(cls, parent_window, defaultDirectory):
        """
        opens a QFileDialog for importing files
        """
        options = QFileDialog.Options(QFileDialog.ShowDirsOnly)
        return QFileDialog.getExistingDirectory(
            parent_window, "Select Model", defaultDirectory, options=options
        )

    @pyqtSlot()
    @threadRouted
    def handleLabelSelectionChange(self):
        enabled = False
        if self.topLevelOperatorView.LabelNames.ready():
            enabled = True
            enabled &= len(self.topLevelOperatorView.LabelNames.value) >= 2
            enabled &= numpy.all(numpy.asarray(self.topLevelOperatorView.InputImages.meta.shape) > 0)

            self.labelingDrawerUi.livePrediction.setChecked(False)
            self._viewerControlUi.checkShowPredictions.setChecked(False)
            self.handleShowPredictionsClicked()

        self._viewerControlUi.checkShowPredictions.setEnabled(enabled)

    def _getNext(self, slot, parentFun, transform=None):
        numLabels = self.labelListData.rowCount()
        value = slot.value
        if numLabels < len(value):
            result = value[numLabels]
            if transform is not None:
                result = transform(result)
            return result
        else:
            return parentFun()

    def _onLabelChanged(self, parentFun, mapf, slot):
        parentFun()
        new = list(map(mapf, self.labelListData))
        old = slot.value
        slot.setValue(_listReplace(old, new))

    def _onLabelRemoved(self, parent, start, end):
        # Call the base class to update the operator.
        super(NNClassGui, self)._onLabelRemoved(parent, start, end)

        # Keep colors in sync with names
        # (If we deleted a name, delete its corresponding colors, too.)
        op = self.topLevelOperatorView
        if len(op.PmapColors.value) > len(op.LabelNames.value):
            for slot in (op.LabelColors, op.PmapColors):
                value = slot.value
                value.pop(start)
                # Force dirty propagation even though the list id is unchanged.
                slot.setValue(value, check_changed=False)

    def getNextLabelName(self):
        return self._getNext(self.topLevelOperatorView.LabelNames, super(NNClassGui, self).getNextLabelName)

    def getNextLabelColor(self):
        return self._getNext(
            self.topLevelOperatorView.LabelColors, super(NNClassGui, self).getNextLabelColor, lambda x: QColor(*x)
        )

    def getNextPmapColor(self):
        return self._getNext(
            self.topLevelOperatorView.PmapColors, super(NNClassGui, self).getNextPmapColor, lambda x: QColor(*x)
        )

    def onLabelNameChanged(self):
        self._onLabelChanged(
            super(NNClassGui, self).onLabelNameChanged, lambda l: l.name, self.topLevelOperatorView.LabelNames
        )

    def onLabelColorChanged(self):
        self._onLabelChanged(
            super(NNClassGui, self).onLabelColorChanged,
            lambda l: (l.brushColor().red(), l.brushColor().green(), l.brushColor().blue()),
            self.topLevelOperatorView.LabelColors,
        )

    def onPmapColorChanged(self):
        self._onLabelChanged(
            super(NNClassGui, self).onPmapColorChanged,
            lambda l: (l.pmapColor().red(), l.pmapColor().green(), l.pmapColor().blue()),
            self.topLevelOperatorView.PmapColors,
        )

    def _update_rendering(self):
        if not self.render:
            return
        shape = self.topLevelOperatorView.InputImages.meta.shape[1:4]
        if len(shape) != 5:
            # this might be a 2D image, no need for updating any 3D stuff
            return

        time = self.editor.posModel.slicingPos5D[0]
        if not self._renderMgr.ready:
            self._renderMgr.setup(shape)

        layernames = set(layer.name for layer in self.layerstack)
        self._renderedLayers = dict((k, v) for k, v in self._renderedLayers.items() if k in layernames)

        newvolume = numpy.zeros(shape, dtype=numpy.uint8)
        for layer in self.layerstack:
            try:
                label = self._renderedLayers[layer.name]
            except KeyError:
                continue
            for ds in layer.datasources:
                vol = ds.dataSlot.value[time, ..., 0]
                indices = numpy.where(vol != 0)
                newvolume[indices] = label

        self._renderMgr.volume = newvolume
        self._update_colors()
        self._renderMgr.update()

    def _update_colors(self):
        for layer in self.layerstack:
            try:
                label = self._renderedLayers[layer.name]
            except KeyError:
                continue
            color = layer.tintColor
            color = (old_div(color.red(), 255.0), old_div(color.green(), 255.0), old_div(color.blue(), 255.0))
            self._renderMgr.setColor(label, color)
