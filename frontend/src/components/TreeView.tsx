/**
 * TreeView Component
 * Reusable component for displaying hierarchical data with expand/collapse
 */
import React, { useState } from 'react';
import '../styles/TreeView.css';

export interface TreeNode {
  id: number | string;
  name: string;
  children?: TreeNode[];
  [key: string]: any;
}

interface TreeViewProps {
  nodes: TreeNode[];
  renderNode: (node: TreeNode, level: number) => React.ReactNode;
  defaultExpanded?: boolean;
  onNodeClick?: (node: TreeNode) => void;
}

const TreeView: React.FC<TreeViewProps> = ({
  nodes,
  renderNode,
  defaultExpanded = false,
  onNodeClick,
}) => {
  const [expandedNodes, setExpandedNodes] = useState<Set<number | string>>(
    new Set(
      defaultExpanded && nodes && Array.isArray(nodes)
        ? nodes.filter((n) => n && n.id !== undefined && n.id !== null).map((n) => n.id)
        : []
    )
  );

  const toggleNode = (nodeId: number | string) => {
    setExpandedNodes((prev) => {
      const newSet = new Set(prev);
      if (newSet.has(nodeId)) {
        newSet.delete(nodeId);
      } else {
        newSet.add(nodeId);
      }
      return newSet;
    });
  };

  const renderTree = (nodeList: TreeNode[], level: number = 0): React.ReactNode => {
    if (!nodeList || !Array.isArray(nodeList)) {
      console.warn('TreeView: Invalid nodeList provided', nodeList);
      return null;
    }

    return nodeList.map((node, index) => {
      // Defensive checks for node validity
      if (!node || node.id === undefined || node.id === null) {
        console.warn('TreeView: Invalid node detected', node);
        return (
          <div key={`invalid-${index}`} className="tree-node error">
            <div className="tree-node-content">Invalid node data</div>
          </div>
        );
      }

      const hasChildren = node.children && Array.isArray(node.children) && node.children.length > 0;
      const isExpanded = expandedNodes.has(node.id);

      return (
        <div key={node.id} className="tree-node">
          <div className="tree-node-content" style={{ paddingLeft: `${level * 20}px` }}>
            {hasChildren && (
              <button
                className="tree-toggle"
                onClick={() => toggleNode(node.id)}
                aria-expanded={isExpanded}
              >
                {isExpanded ? '▼' : '▶'}
              </button>
            )}
            {!hasChildren && <span className="tree-spacer" />}
            <div
              className="tree-node-body"
              onClick={() => onNodeClick && onNodeClick(node)}
            >
              {renderNode(node, level)}
            </div>
          </div>
          {hasChildren && isExpanded && (
            <div className="tree-children">
              {renderTree(node.children!, level + 1)}
            </div>
          )}
        </div>
      );
    });
  };

  if (!nodes || !Array.isArray(nodes)) {
    console.warn('TreeView: Invalid nodes prop provided', nodes);
    return <div className="tree-view error">Invalid tree data</div>;
  }

  return <div className="tree-view">{renderTree(nodes)}</div>;
};

export default TreeView;
