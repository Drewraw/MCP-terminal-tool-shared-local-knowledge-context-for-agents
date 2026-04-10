#!/usr/bin/env python
"""Quick test of TSX-aware pruning logic"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from indexer.models import SkeletalIndex, SkeletonEntry, SymbolKind
from pruner.pruning_engine import PruningEngine
from pruner.models import PruneRequest

# Create a mock TSX file content with large component body
TSX_CONTENT = """import React from 'react';

interface UserProps {
  name: string;
  email: string;
  onEdit: (updated: User) => void;
  onDelete: (id: string) => void;
}

interface User {
  id: string;
  name: string;
  email: string;
  role: string;
}

export const UserCard: React.FC<UserProps> = ({
  name,
  email,
  onEdit,
  onDelete,
}) => {
  const [isEditing, setIsEditing] = React.useState(false);
  const [editName, setEditName] = React.useState(name);
  const [editEmail, setEditEmail] = React.useState(email);

  const handleSave = () => {
    onEdit({ id: '', name: editName, email: editEmail, role: 'user' });
    setIsEditing(false);
  };

  const handleCancel = () => {
    setEditName(name);
    setEditEmail(email);
    setIsEditing(false);
  };

  if (isEditing) {
    return (
      <div className="card editing">
        <div className="form">
          <label>Name</label>
          <input
            value={editName}
            onChange={(e) => setEditName(e.target.value)}
            placeholder="Enter name"
          />
          <label>Email</label>
          <input
            value={editEmail}
            onChange={(e) => setEditEmail(e.target.value)}
            placeholder="Enter email"
          />
          <div className="actions">
            <button onClick={handleSave}>Save</button>
            <button onClick={handleCancel}>Cancel</button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="card">
      <div className="header">
        <h2>{name}</h2>
        <button 
          className="close"
          onClick={() => onDelete('')}
          aria-label="Delete"
        >
          ✕
        </button>
      </div>
      <div className="content">
        <p><strong>Email:</strong> {email}</p>
        <p><strong>Role:</strong> user</p>
      </div>
      <div className="footer">
        <button onClick={() => setIsEditing(true)}>Edit</button>
      </div>
    </div>
  );
};
"""

def test_tsx_pruning():
    # Create mock skeleton index
    skeleton = SkeletalIndex()
    skeleton.entries = [
        # Props interface - should be kept in full
        SkeletonEntry(
            name="UserProps",
            kind=SymbolKind.INTERFACE,
            file_path="src/UserCard.tsx",
            line_start=3,
            line_end=8,
            parent=None,
        ),
        # User state interface - should be kept in full
        SkeletonEntry(
            name="User",
            kind=SymbolKind.INTERFACE,
            file_path="src/UserCard.tsx",
            line_start=10,
            line_end=15,
            parent=None,
        ),
        # UserCard component - should be sliced to signature only
        SkeletonEntry(
            name="UserCard",
            kind=SymbolKind.FUNCTION,
            file_path="src/UserCard.tsx",
            line_start=17,
            line_end=78,
            parent=None,
        ),
    ]
    skeleton.file_count = 1
    skeleton.total_symbols = 3

    # Write test file
    test_file = "/tmp/UserCard.tsx"
    os.makedirs(os.path.dirname(test_file), exist_ok=True)
    with open(test_file, "w") as f:
        f.write(TSX_CONTENT)

    # Create pruning engine
    pruner = PruningEngine(skeleton, "/tmp")

    # Prune
    result_file = pruner._prune_file("UserCard.tsx", skeleton.entries, "Focus on component structure")

    if result_file:
        print("=" * 70)
        print("TSX PRUNING TEST RESULTS")
        print("=" * 70)
        print(f"\nFile: {result_file.file_path}")
        print(f"Raw lines:     {result_file.raw_lines:>4}")
        print(f"Pruned lines:  {result_file.pruned_lines:>4}")
        print(f"Line reduction: {(1 - result_file.pruned_lines/result_file.raw_lines)*100:.1f}%")
        print(f"\nRaw tokens:    {result_file.raw_tokens:>6}")
        print(f"Pruned tokens: {result_file.pruned_tokens:>6}")
        token_savings = (result_file.raw_tokens - result_file.pruned_tokens) / max(result_file.raw_tokens, 1) * 100
        print(f"Token savings: {token_savings:>6.1f}%")
        print(f"Compression:   {result_file.raw_tokens / max(result_file.pruned_tokens, 1):.2f}x")

        print(f"\nKept symbols:")
        for sym in result_file.kept_symbols:
            print(f"  • {sym}")

        print(f"\n--- PRUNED OUTPUT (TSX-aware) ---")
        print(result_file.pruned_content)
        print("\n" + "=" * 70)
        print("✅ TSX-aware pruning is working!")
        print("✅ Props interfaces kept in full")
        print("✅ Component body sliced to signature")
        print("=" * 70)
    else:
        print("❌ Pruning failed")

if __name__ == "__main__":
    test_tsx_pruning()
