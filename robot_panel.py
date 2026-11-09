"""Offline xArm jog preview and one-shot, read-only controller telemetry."""

from ipaddress import IPv4Address
import json
from math import isfinite
from pathlib import Path
import sys

from PySide6 import QtCore, QtWidgets as Qt


RESULT_PREFIX = "NEURONAV_XARM_RESULT "
START_POSITION_MM = (300, 0, 200)


def read_pose(address):
    try:
        from xarm.wrapper import XArmAPI
    except ModuleNotFoundError as exc:
        raise RuntimeError("Install xarm-python-sdk in the NeuroNav Python environment.") from exc

    arm = XArmAPI(address, do_not_open=True)
    try:
        arm.connect()
        if not arm.connected:
            raise RuntimeError("Could not connect to the xArm controller.")
        code, values = arm.get_position(is_radian=False)
        if code != 0:
            raise RuntimeError(f"xArm pose query returned SDK code {code}.")
        pose = tuple(map(float, values))
        if len(pose) != 6 or not all(map(isfinite, pose)):
            raise RuntimeError("The controller returned an invalid pose.")
        return pose
    finally:
        arm.disconnect()


class RobotPanel(Qt.QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("xArm")
        self.setMinimumWidth(450)
        layout = Qt.QVBoxLayout(self)
        preview = Qt.QGroupBox("Offline jog preview")
        jog = Qt.QGridLayout(preview)
        description = Qt.QLabel(
            "Try Cartesian jogs in robot-base coordinates. This preview updates "
            "the simulated position below and uses no hardware."
        )
        description.setWordWrap(True)
        jog.addWidget(description, 0, 0, 1, 3)
        self.preview_position = Qt.QLabel()
        self.step = Qt.QSpinBox()
        self.step.setRange(1, 25)
        self.step.setValue(5)
        self.step.setSuffix(" mm")
        jog.addWidget(Qt.QLabel("Jog step"), 1, 0)
        jog.addWidget(self.step, 1, 1, 1, 2)
        for axis, name in enumerate("XYZ"):
            jog.addWidget(Qt.QLabel(name), axis + 2, 0)
            for column, direction in enumerate((-1, 1), 1):
                button = Qt.QPushButton("−" if direction < 0 else "+")
                button.setAccessibleName(f"Jog {name} {'negative' if direction < 0 else 'positive'}")
                button.clicked.connect(
                    lambda checked=False, axis=axis, direction=direction: self.jog(axis, direction)
                )
                jog.addWidget(button, axis + 2, column)
        reset = Qt.QPushButton("Reset preview")
        reset.clicked.connect(self.reset_preview)
        jog.addWidget(self.preview_position, 5, 0, 1, 2)
        jog.addWidget(reset, 5, 2)
        layout.addWidget(preview)
        self.reset_preview()

        telemetry = Qt.QGroupBox("Controller pose snapshot · read-only")
        form = Qt.QFormLayout(telemetry)
        self.address = Qt.QLineEdit()
        self.address.setPlaceholderText("Controller IPv4 address")
        self.read_button = Qt.QPushButton("Read pose")
        self.read_button.clicked.connect(self.start_read)
        form.addRow("Controller IP", self.address)
        form.addRow(self.read_button)
        self.status = Qt.QLabel("Enter the IP of the powered xArm controller.")
        self.status.setWordWrap(True)
        self.status.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        form.addRow(self.status)
        layout.addWidget(telemetry)

        close = Qt.QDialogButtonBox(Qt.QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.process = QtCore.QProcess(self)
        self.process.finished.connect(self.read_finished)
        self.process.errorOccurred.connect(self.read_error)
        self.timeout = QtCore.QTimer(self, singleShot=True, interval=8000)
        self.timeout.timeout.connect(self.read_timeout)
        self.finished.connect(self.stop_read)

    def jog(self, axis, direction):
        self.position[axis] += direction * self.step.value()
        self.update_preview()

    def reset_preview(self):
        self.position = list(START_POSITION_MM)
        self.update_preview()

    def update_preview(self):
        self.preview_position.setText(
            "   ".join(f"{axis} {value} mm" for axis, value in zip("XYZ", self.position))
        )

    def start_read(self):
        try:
            address = str(IPv4Address(self.address.text().strip()))
        except ValueError:
            self.status.setText("Enter a valid IPv4 address.")
            return
        self.read_button.setEnabled(False)
        self.address.setEnabled(False)
        self.status.setText(f"Reading pose from {address}…")
        self.timeout.start()
        self.process.start(sys.executable, [str(Path(__file__).resolve()), address])

    def finish_read(self, message):
        self.timeout.stop()
        self.read_button.setEnabled(True)
        self.address.setEnabled(True)
        self.status.setText(message)

    def read_finished(self, exit_code, _exit_status):
        output = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if not self.timeout.isActive():
            self.finish_read(self.status.text())
            return
        for line in reversed(output.splitlines()):
            if line.startswith(RESULT_PREFIX):
                result = json.loads(line[len(RESULT_PREFIX):])
                if "error" in result:
                    self.finish_read(result["error"])
                else:
                    fields = zip(("X", "Y", "Z", "Roll", "Pitch", "Yaw"),
                                 result["pose"], ("mm", "mm", "mm", "°", "°", "°"))
                    self.finish_read("Robot-base TCP snapshot\n" + "   ".join(
                        f"{name} {value:.2f} {unit}" for name, value, unit in fields
                    ))
                return
        self.finish_read(f"Pose reader exited without a result (exit code {exit_code}).")

    def read_error(self, error):
        if error == QtCore.QProcess.ProcessError.FailedToStart:
            self.finish_read("Could not start the xArm pose reader.")

    def read_timeout(self):
        self.status.setText("Pose read timed out. Check controller power, Ethernet and IP.")
        # Wait for the asynchronous exit before permitting another request.
        self.process.kill()

    def stop_read(self):
        self.timeout.stop()
        if self.process.state() != QtCore.QProcess.ProcessState.NotRunning:
            self.process.kill()
            self.process.waitForFinished(1000)


if __name__ == "__main__":
    try:
        result = {"pose": read_pose(str(IPv4Address(sys.argv[1])))}
    except Exception as exc:
        result = {"error": str(exc) or type(exc).__name__}
    print(RESULT_PREFIX + json.dumps(result), flush=True)
