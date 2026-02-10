import os
import hou
from PySide6 import QtWidgets
from HOUDINI_HIP_MANAGER.ui.dialogs import AttributeConfigDialog
from HOUDINI_HIP_MANAGER.utils.common_utils import load_config

class AssetChecker:
    """资产检查器类，负责检查和导出Houdini资产"""
    
    def __init__(self, parent_window):
        self.parent_window = parent_window
        self.allowed_attrs = {"point": "", "prim": "", "vertex": "", "detail": ""}
        self.load_config()
    
    def load_config(self):
        """加载属性配置"""
        config, _ = load_config("asset_checker")
        for key, value in config.items():
            if key in self.allowed_attrs:
                self.allowed_attrs[key] = value
    
    def check_node_attributes(self, node):
        """检查节点属性是否符合配置"""
        if not isinstance(node, hou.SopNode):
            return False, "仅支持SOP节点"
        
        try:
            geo = node.geometry()
            if not geo:
                return False, "节点无几何体"
            
            issues = []
            
            point_attrs = set(attr.name() for attr in geo.pointAttribs())
            allowed_point_attrs = set(self.allowed_attrs.get("point", "").split(",") if self.allowed_attrs.get("point") else [])
            invalid_point_attrs = point_attrs - allowed_point_attrs
            if invalid_point_attrs:
                issues.append(f"点属性包含未允许的属性: {', '.join(invalid_point_attrs)}")
            
            prim_attrs = set(attr.name() for attr in geo.primAttribs())
            allowed_prim_attrs = set(self.allowed_attrs.get("prim", "").split(",") if self.allowed_attrs.get("prim") else [])
            invalid_prim_attrs = prim_attrs - allowed_prim_attrs
            if invalid_prim_attrs:
                issues.append(f"图元属性包含未允许的属性: {', '.join(invalid_prim_attrs)}")
            
            vertex_attrs = set(attr.name() for attr in geo.vertexAttribs())
            allowed_vertex_attrs = set(self.allowed_attrs.get("vertex", "").split(",") if self.allowed_attrs.get("vertex") else [])
            invalid_vertex_attrs = vertex_attrs - allowed_vertex_attrs
            if invalid_vertex_attrs:
                issues.append(f"顶点属性包含未允许的属性: {', '.join(invalid_vertex_attrs)}")
            
            detail_attrs = set(attr.name() for attr in geo.globalAttribs())
            allowed_detail_attrs = set(self.allowed_attrs.get("detail", "").split(",") if self.allowed_attrs.get("detail") else [])
            invalid_detail_attrs = detail_attrs - allowed_detail_attrs
            if invalid_detail_attrs:
                issues.append(f"细节属性包含未允许的属性: {', '.join(invalid_detail_attrs)}")
            
            if issues:
                return False, "\n".join(issues)
            
            return True, f"检测通过！点数: {geo.intrinsicValue('pointcount')}, 图元数: {geo.intrinsicValue('primitivecount')}\n请检查是否遗漏属性或点/图元数量是否符合预期。"
        except Exception as e:
            QtWidgets.QMessageBox.critical(self.parent_window, "错误", f"检查属性失败:\n{e}", QtWidgets.QMessageBox.Ok)
            return False, f"检查属性失败: {e}"
    
    def configure_allowed_attributes(self):
        """配置允许的属性"""
        dialog = AttributeConfigDialog(self.parent_window)
        if dialog.exec_():
            self.allowed_attrs = dialog.allowed_attrs
            return True
        return False
    
    def check_selected_node(self, log_widget, status_label):
        """检查选中的节点"""
        selected_nodes = hou.selectedNodes()
        if not selected_nodes:
            warning_msg = "未选择任何节点"
            log_widget.add_log(warning_msg, "warning")
            status_label.setText(warning_msg)
            return False
        
        node = selected_nodes[0]
        success, message = self.check_node_attributes(node)
        if success:
            try:
                parent_network = node.parent()
                rop_node = parent_network.createNode("rop_geometry", f"rop_{node.name()}")
                rop_node.parm("soppath").set(node.path())
                rop_node.setInput(0, node)
                rop_node.moveToGoodPosition()
                
                success_msg = f"检测通过，已创建ROP节点: {rop_node.path()}"
                log_widget.add_log(success_msg, "success")
                status_label.setText(success_msg)
                
                QtWidgets.QMessageBox.information(self.parent_window, "成功", f"{message}\n已创建ROP节点: {rop_node.path()}", QtWidgets.QMessageBox.Ok)
                return True
            except Exception as e:
                error_msg = f"创建ROP节点失败: {e}"
                log_widget.add_log(error_msg, "error")
                status_label.setText(error_msg)
                return False
        else:
            warning_msg = f"属性检查失败: {message}"
            log_widget.add_log(warning_msg, "warning")
            status_label.setText("属性检查失败，导出被禁止")
            
            QtWidgets.QMessageBox.warning(self.parent_window, "警告", f"属性检查失败:\n{message}\n导出被禁止。", QtWidgets.QMessageBox.Ok)
            return False
    
    def export_asset(self, log_widget, status_label):
        """导出资产"""
        selected_nodes = hou.selectedNodes()
        if not selected_nodes:
            warning_msg = "未选择任何节点"
            log_widget.add_log(warning_msg, "warning")
            status_label.setText(warning_msg)
            return False
        
        node = selected_nodes[0]
        success, message = self.check_node_attributes(node)
        if not success:
            warning_msg = f"属性检查失败: {message}"
            log_widget.add_log(warning_msg, "warning")
            status_label.setText("属性检查失败，导出被禁止")
            
            QtWidgets.QMessageBox.warning(self.parent_window, "警告", f"属性检查失败:\n{message}\n导出被禁止。", QtWidgets.QMessageBox.Ok)
            return False

        save_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self.parent_window, "导出资产", "", "Geometry Files (*.bgeo *.bgeo.sc *.geo *.geo.sc)"
        )
        if save_path:
            try:
                parent_network = node.parent()
                rop_node = parent_network.createNode("rop_geometry", f"export_{node.name()}")
                rop_node.parm("soppath").set(node.path())
                rop_node.parm("sopoutput").set(save_path)
                rop_node.setInput(0, node)
                rop_node.moveToGoodPosition()
                rop_node.render()
                
                success_msg = f"资产导出成功: {os.path.basename(save_path)}"
                log_widget.add_log(success_msg, "success")
                log_widget.add_history(node.path(), save_path)
                status_label.setText(success_msg)
                return True
            except Exception as e:
                error_msg = f"导出失败: {e}"
                log_widget.add_log(error_msg, "error")
                status_label.setText(error_msg)
                return False
        return False
